########################################################################
# Discord 年齡驗證 Bot  (Slash only · EasyOCR+Tesseract · Render OK)
########################################################################
# requirements.txt：
# discord.py>=2.3
# easyocr
# pytesseract
# opencv-python-headless
# flask
########################################################################

import os, re, uuid, asyncio, logging
from datetime import datetime
from threading import Thread

import cv2, numpy as np, pytesseract, easyocr
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# ======== 參數設定 ========
GUILD_ID           = 1364979218697687100   # ← 換成你的伺服器 ID
VERIFIED_ROLE_NAME = "成年妹寶"
MINIMUM_AGE        = 18

# ======== Discord Bot ========
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ======== Flask 保活 ========
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is alive!"
Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()

# ======== OCR 工具 ========
reader = easyocr.Reader(['ch_tra', 'en'], gpu=False)

def extract_date(text: str):
    ymd = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    dmy = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', text)
    if ymd:
        y, m, d = ymd.groups()
    elif dmy:
        d, m, y = dmy.groups()
    else:
        return ""
    return f"{y}-{m}-{d}"

def tesseract_gray(gray: np.ndarray):
    clahe = cv2.createCLAHE(2.0, (8, 8))
    gray  = clahe.apply(gray)
    gray  = cv2.medianBlur(gray, 3)
    sharp = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    gray  = cv2.filter2D(gray, -1, sharp)
    bw = cv2.adaptiveThreshold(gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 11, 2)
    h,_ = bw.shape
    mask = np.ones_like(bw)*255
    mask[:int(h*0.4),:] = 0
    final = cv2.bitwise_or(bw, mask)
    cfg = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789/-'
    raw = pytesseract.image_to_string(final, lang='eng', config=cfg)
    return raw, extract_date(raw)

def ocr_bytes(b: bytes):
    img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    gray= cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    easy = " ".join(reader.readtext(img, detail=0, paragraph=False))
    date = extract_date(easy)
    return (easy, date) if date else tesseract_gray(gray)

def age_from(date: str):
    if not date:
        return None, None
    y, m, d = map(int, date.split('-'))
    try:
        bd  = datetime(y, m, d)
        now = datetime.now()
        age = now.year - bd.year - ((now.month,now.day) < (bd.month,bd.day))
        return age, f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None, None

# ======== 驗證流程 ========
async def verify_flow(guild, member, inter):
    role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if role and role in member.roles:
        await inter.response.send_message("✅ 你已經通過驗證", ephemeral=True)
        return

    overw = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member           : discord.PermissionOverwrite(view_channel=True, send_messages=True),
        guild.me         : discord.PermissionOverwrite(view_channel=True, send_messages=True)
    }
    channel = await guild.create_text_channel(f"verify-{uuid.uuid4().hex[:6]}", overwrites=overw)
    await inter.response.send_message(f"已建立驗證頻道：{channel.mention}", ephemeral=True)

    await channel.send(f"{member.mention} 請上傳僅含出生日期的證件照片，10 分鐘內有效")

    def img_check(m): return m.author==member and m.channel==channel and m.attachments
    age_ok = False
    try:
        msg = await bot.wait_for("message", timeout=600, check=img_check)
        await channel.send("⏳ OCR 辨識中…")
        raw, date = ocr_bytes(await msg.attachments[0].read())
        age, fmt  = age_from(date)
        if age and age >= MINIMUM_AGE:
            await channel.send(f"✅ 生日 {fmt}，{age} 歲，授予身份組…")
            age_ok = True
        else:
            await channel.send("⚠️ 無法辨識日期，請手動輸入 YYYY-MM-DD，5 分鐘內有效")
            def txt_check(m): return m.author==member and m.channel==channel
            try:
                tmsg = await bot.wait_for("message", timeout=300, check=txt_check)
                age, fmt = age_from(tmsg.content)
                if age and age >= MINIMUM_AGE:
                    await channel.send(f"✅ 手動確認 {fmt}，{age} 歲，授予身份組…")
                    age_ok = True
                else:
                    await channel.send("❌ 年齡不足或格式錯誤")
            except asyncio.TimeoutError:
                await channel.send("⌛ 5 分鐘內未收到輸入")
    except asyncio.TimeoutError:
        await channel.send("⌛ 10 分鐘內未收到圖片")

    if age_ok and role:
        try:
            await member.add_roles(role)
        except Exception:
            await channel.send("⚠️ 賦予身份組失敗，請通知管理員")

    await channel.send("頻道將於 15 秒後刪除")
    await asyncio.sleep(15)
    try:
        await channel.delete()
    except Exception:
        pass

# ======== Slash 指令 ========
@bot.tree.command(name="verify", description="開始年齡驗證流程")
async def slash_verify(inter: discord.Interaction):
    await verify_flow(inter.guild, inter.user, inter)

class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🔞 點我開始年齡驗證", style=discord.ButtonStyle.primary)
    async def btn(self, inter: discord.Interaction, _):
        await verify_flow(inter.guild, inter.user, inter)

@bot.tree.command(name="setupverifybutton", description="建立驗證按鈕（管理員）")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup(inter: discord.Interaction):
    await inter.response.send_message("請點擊下方按鈕開始驗證：", view=VerifyButton())

# ======== on_ready：立即同步 ========
@bot.event
async def on_ready():
    print(f"Bot Online: {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to {GUILD_ID}")
    except Exception as e:
        print("Slash 同步失敗：", repr(e))
    bot.add_view(VerifyButton())

# ======== 主程式入口 ========
if __name__ == "__main__":
    token = os.getenv("BOT_TOKEN")
    if not token:
        logging.error("BOT_TOKEN 環境變數未設定")
    else:
        bot.run(token)
