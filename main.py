# ------------ 依賴 ------------
# requirements.txt 需含：
# discord.py>=2.3
# easyocr
# pytesseract
# opencv-python-headless
# flask

import os, re, uuid, asyncio, logging
from datetime import datetime
from threading import Thread

import cv2, numpy as np, pytesseract, easyocr
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# ------------ 基本設定 ------------
GUILD_ID           = 1364979218697687100   # 換成你的伺服器 ID
VERIFIED_ROLE_NAME = "成年妹寶"
MINIMUM_AGE        = 18

# ------------ Discord ------------
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(intents=intents)

# ------------ Flask 保活 ------------
app = Flask(__name__)
@app.route("/")
def home(): return "Bot OK"
Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()

# ------------ OCR ------------
reader = easyocr.Reader(['en', 'ch_tra', 'ch_sim'], gpu=False)

def extract_date(text:str):
    p_ymd = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    p_dmy = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', text)
    if p_ymd: y,m,d = p_ymd.groups()
    elif p_dmy: d,m,y = p_dmy.groups()
    else: return ""
    return f"{y}-{m}-{d}"

def tesseract(gray):
    clahe = cv2.createCLAHE(2.0,(8,8)); gray=clahe.apply(gray)
    gray = cv2.medianBlur(gray,3)
    sharp = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    gray = cv2.filter2D(gray,-1,sharp)
    bw = cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV,11,2)
    h,_ = bw.shape; mask=np.ones_like(bw)*255; mask[:int(h*0.4),:]=0
    img = cv2.bitwise_or(bw,mask)
    cfg = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789/-'
    raw = pytesseract.image_to_string(img,lang='eng',config=cfg)
    return raw, extract_date(raw)

def ocr_bytes(b:bytes):
    img = cv2.imdecode(np.frombuffer(b,np.uint8),cv2.IMREAD_COLOR)
    gray= cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    ez  = " ".join(reader.readtext(img,detail=0,paragraph=False))
    dt  = extract_date(ez)
    return (ez,dt) if dt else tesseract(gray)

def age_from(date:str):
    if not date: return None,None
    y,m,d = map(int,date.split('-'))
    try:
        bd=datetime(y,m,d); now=datetime.now()
        age=now.year-bd.year-((now.month,now.day)<(bd.month,bd.day))
        return age,f"{y:04d}-{m:02d}-{d:02d}"
    except: return None,None

# ------------ 核心流程 ------------
async def verify_flow(guild, member, inter_src):
    role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if role and role in member.roles:
        await inter_src.response.send_message("✅ 你已驗證過",ephemeral=True); return

    overw = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
             member: discord.PermissionOverwrite(view_channel=True,send_messages=True),
             guild.me: discord.PermissionOverwrite(view_channel=True,send_messages=True)}
    ch = await guild.create_text_channel(f"verify-{uuid.uuid4().hex[:6]}",overwrites=overw)
    await inter_src.response.send_message(f"已建立 {ch.mention}",ephemeral=True)

    await ch.send(f"{member.mention} 請上傳含出生日期圖片，10 分鐘內有效")
    def chk(m): return m.author==member and m.channel==ch and m.attachments
    try:
        msg = await bot.wait_for("message",timeout=600,check=chk)
        raw,date = ocr_bytes(await msg.attachments[0].read())
        age,fmt  = age_from(date)
        if age and age>=MINIMUM_AGE:
            await ch.send(f"✅ 生日 {fmt}，年齡 {age} 歲，授予身份組")
            if role: await member.add_roles(role)
        else:
            await ch.send("❌ 年齡不足或日期辨識失敗")
    except asyncio.TimeoutError:
        await ch.send("⌛ 逾時未收到圖片")
    await ch.send("頻道將在 60 秒後刪除"); await asyncio.sleep(60)
    try: await ch.delete(); except: pass

# ------------ Slash 指令 ------------
@bot.tree.command(name="verify",description="開始年齡驗證")
async def slash_verify(inter: discord.Interaction):
    await inter.response.defer(thinking=True,ephemeral=True)
    await verify_flow(inter.guild, inter.user, inter)

class VerifyButton(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🔞 點我開始年齡驗證",style=discord.ButtonStyle.primary)
    async def callback(self,inter:discord.Interaction,btn):
        await inter.response.defer(ephemeral=True)
        await verify_flow(inter.guild, inter.user, inter)

@bot.tree.command(name="setupverifybutton",description="建立驗證按鈕（管理員）")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup(inter: discord.Interaction):
    await inter.response.send_message("請點擊下方按鈕完成驗證",view=VerifyButton())

# ------------ on_ready：立即同步 Slash ------------
@bot.event
async def on_ready():
    print(f"Bot Online: {bot.user}")
    if GUILD_ID:
        guild=discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)  # 當前伺服器即時同步
    bot.add_view(VerifyButton())

# ------------ 入口 ------------
if __name__=="__main__":
    TOKEN=os.getenv("BOT_TOKEN")
    if not TOKEN:
        logging.error("BOT_TOKEN 未設定")
    else:
        bot.run(TOKEN)
