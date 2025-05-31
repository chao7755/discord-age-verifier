#######################################################################
#  Discord 年齡驗證 Bot ‧ Render 版
#  - 雙引擎 OCR：EasyOCR → fallback Tesseract
#  - 香港證件日期 (DD-MM-YYYY) 最佳化
#  - Flask keep-alive
#######################################################################

import os, re, uuid, asyncio, logging
from datetime import datetime
from threading import Thread

import cv2
import numpy as np
import pytesseract
import easyocr

import discord
from discord.ext import commands
from flask import Flask

############### 基本設定 ################################################
GUILD_ID           = 1364979218697687100           #〈—改成自己的伺服器 ID
VERIFIED_ROLE_NAME = "成年妹寶"
MINIMUM_AGE        = 18

################ Discord Bot ###########################################
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

################ Flask 保活 ############################################
app = Flask(__name__)
@app.route("/")
def home(): return "Bot running!"
def run_keep_alive():
    app.run(host="0.0.0.0", port=8080)
Thread(target=run_keep_alive, daemon=True).start()

################ OCR：EasyOCR + Tesseract fallback #####################
reader = easyocr.Reader(['en', 'ch_tra', 'ch_sim'], gpu=False)

def tesseract_fallback(gray: np.ndarray):
    """加強版 Tesseract OCR，傳入灰階圖片 → (raw_text, date_str)"""
    # CLAHE 對比
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray  = clahe.apply(gray)
    # 去噪 + 銳化
    gray  = cv2.medianBlur(gray, 3)
    kernel= np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    gray  = cv2.filter2D(gray, -1, kernel)
    # 反色二值
    bw = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 11, 2)
    # 遮掉上半的「出生日期」中文字
    h, _ = bw.shape
    mask = np.ones_like(bw) * 255
    mask[: int(h*0.4), :] = 0
    final = cv2.bitwise_or(bw, mask)

    cfg = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789/-'
    raw = pytesseract.image_to_string(final, lang='eng', config=cfg)
    return raw, extract_date(raw)

def extract_date(text: str):
    """從文字中撈出 YYYY-MM-DD 或 DD-MM-YYYY，回傳標準 YYYY-MM-DD"""
    p1 = re.search(r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b', text)
    p2 = re.search(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b', text)
    if p1:
        y,m,d = p1.groups()
    elif p2:
        d,m,y = p2.groups()
    else:
        return ""
    return f"{y}-{m}-{d}"

def ocr_date_bytes(img_bytes: bytes):
    """先 EasyOCR ，失敗再 Tesseract；回傳 (raw_text, date_str)"""
    img  = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # EasyOCR
    segments = reader.readtext(img, detail=0, paragraph=False)
    text = " ".join(segments)
    date = extract_date(text)

    if date:
        return text, date
    # fallback
    return tesseract_fallback(gray)

################ 年齡計算 ##############################################
def parse_birthdate_to_age(date_str: str):
    if not date_str: return None, None
    ds = date_str.replace('/', '-').replace('.', '-')
    y,m,d = None, None, None
    m1 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', ds)
    m2 = re.match(r'(\d{1,2})-(\d{1,2})-(\d{4})', ds)
    if m1:
        y,m,d = map(int, m1.groups())
    elif m2:
        d,m,y = map(int, m2.groups())
    if not y: return None, None
    try:
        bd  = datetime(y,m,d)
        now = datetime.now()
        age = now.year - bd.year - ((now.month,now.day) < (bd.month,bd.day))
        return age, f"{y:04d}-{m:02d}-{d:02d}"
    except: return None, None

################ 驗證流程 ##############################################
async def start_verify_flow(guild, member, source):
    role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if role and role in member.roles:
        await _ephemeral(source, "✅ 你已通過驗證") ; return

    ov = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member           : discord.PermissionOverwrite(
                             view_channel=True, send_messages=True,
                             read_message_history=True),
        guild.me         : discord.PermissionOverwrite(
                             view_channel=True, send_messages=True,
                             read_message_history=True)
    }
    cname = f"verify-{member.name}-{uuid.uuid4().hex[:6]}"
    try:
        ch = await guild.create_text_channel(cname, overwrites=ov)
        await _ephemeral(source, f"✅ 已建立驗證頻道：{ch.mention}")
    except:
        await _ephemeral(source, "❌ 建立頻道失敗，檢查權限") ; return

    await ch.send(
        f"{member.mention} 請上傳**僅含出生日期**的證件照片，10 分鐘內有效。\n"
        "建議裁切至日期區域，避免背光與反光。")

    def img_chk(m): return m.author==member and m.channel==ch and m.attachments
    age_ok = False
    try:
        msg = await bot.wait_for("message", timeout=600, check=img_chk)
        await ch.send("⏳ AI OCR 處理中…")
        raw, date = ocr_date_bytes(await msg.attachments[0].read())
        age, fmt  = parse_birthdate_to_age(date)
        if age and age >= MINIMUM_AGE:
            await ch.send(f"✅ 生日 {fmt}，{age} 歲，賦予身份組…")
            age_ok = True
        elif age:
            await ch.send(f"❌ 未滿 {MINIMUM_AGE} 歲（生日 {fmt}）")
        else:
            await ch.send("⚠️ 日期解析失敗，請手動輸入 YYYY-MM-DD")
    except asyncio.TimeoutError:
        await ch.send("⌛ 上傳逾時")

    if not age_ok:
        await ch.send("請手動輸入生日（YYYY-MM-DD），5 分鐘內有效")
        def txt_chk(m): return m.author==member and m.channel==ch
        try:
            msg = await bot.wait_for("message", timeout=300, check=txt_chk)
            age, fmt = parse_birthdate_to_age(msg.content)
            if age and age >= MINIMUM_AGE:
                await ch.send(f"✅ 手動確認 {fmt}，{age} 歲，賦予身份組…")
                age_ok = True
            else:
                await ch.send("❌ 年齡不足或格式錯誤")
        except asyncio.TimeoutError:
            await ch.send("⌛ 未收到輸入")

    if age_ok and role:
        try: await member.add_roles(role)
        except: await ch.send("⚠️ 加身份組失敗，請通知管理員")

    await ch.send("頻道將於 60 秒後刪除") ; await asyncio.sleep(60)
    try: await ch.delete() ; except: pass

async def _ephemeral(src, txt):
    if isinstance(src, discord.Interaction):
        if not src.response.is_done():
            await src.response.send_message(txt, ephemeral=True)
        else:
            await src.followup.send(txt, ephemeral=True)
    else: await src.send(txt, delete_after=30)

################ 指令與按鈕 ###########################################
@bot.tree.command(name="verify", description="開始年齡驗證")
async def verify_slash(inter: discord.Interaction):
    asyncio.create_task(start_verify_flow(inter.guild, inter.user, inter))

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🔞 點我開始年齡驗證",
                       style=discord.ButtonStyle.primary,
                       custom_id="verify_btn")
    async def btn(self, inter: discord.Interaction, _):
        await inter.response.send_message("正在準備驗證頻道…", ephemeral=True)
        asyncio.create_task(start_verify_flow(inter.guild, inter.user,
                                              inter.followup))

@bot.command(name="setupverifybutton")
@commands.has_permissions(administrator=True)
async def cmd_setup_button(ctx):
    await ctx.send("點擊下方按鈕完成年齡驗證：", view=VerifyView())
    await ctx.message.delete()

@cmd_setup_button.error
async def cmd_err(ctx, err):
    if isinstance(err, commands.MissingPermissions):
        await ctx.send("你沒有權限使用此指令", delete_after=10)

################ on_ready ##############################################
@bot.event
async def on_ready():
    print(f"Bot 已上線：{bot.user}")
    bot.add_view(VerifyView())
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)

################ 啟動 ###################################################
if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        logging.error("環境變數 BOT_TOKEN 未設定！")
    else:
        bot.run(TOKEN)
