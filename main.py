########################  main.py  ########################
# -*- coding: utf-8 -*-
"""
Discord 年齡驗證 Bot（Render 免費方案可用）
---------------------------------------------------------
Environment 變數：
  BOT_TOKEN        Discord Bot Token
  GUILD_ID         伺服器 ID
---------------------------------------------------------
硬碼常數：
  ROLE_ID          通過驗證後要賦予的角色 ID
---------------------------------------------------------
2025-06  by ChatGPT
"""
import os, re, io, gc, asyncio, datetime, logging, threading

import discord
from discord.ext        import commands
from discord            import app_commands, ui, Interaction, Embed

import easyocr
import numpy as np
import cv2                               # easyocr 內部也需用到

from flask import Flask
from waitress import serve
# --------------------------------------------------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s:%(name)s: %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID  = int(os.getenv("GUILD_ID" , 0))

ROLE_ID   = 1375827130063126538          # 「成年妹寶」

if not all([BOT_TOKEN, GUILD_ID, ROLE_ID]):
    raise RuntimeError("❌ 請在 Render 上填好 BOT_TOKEN / GUILD_ID，並確認 ROLE_ID 寫死無誤！")

# ---------- Discord intents ----------
intents                = discord.Intents.default()
intents.members         = True           # 需要讀取身分組
intents.message_content = True           # 後台要勾選「Message Content」

# ---------- Bot & Slash 指令管理器 ----------
bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree                          # ★ 先建好，後面 decorator 會用

# ---------- EasyOCR 讀取器 (英文即可) ----------
logging.info("🔍 Initialising EasyOCR (en)…")
READER = easyocr.Reader(['en'], gpu=False)   # 只載一份，常駐 RAM ≈ 230 MB

ALLOW_CHARS = '0123456789/-'
DATE_RE     = re.compile(r'(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})')

async def ocr_birthdate(img_bytes: bytes) -> str | None:
    """
    從圖片 bytes 取出 'YYYY-MM-DD'，失敗回傳 None
    """
    np_img  = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)

    loop    = asyncio.get_running_loop()
    texts   = await loop.run_in_executor(
        None,
        lambda: READER.readtext(np_img, detail=0,
                                allowlist=ALLOW_CHARS, paragraph=False)
    )

    for line in texts:
        if m := DATE_RE.search(line):
            y, mth, d = m.groups()
            if len(y) == 2:                      # 兩位數年份補齊
                y = '19'+y if int(y) > 30 else '20'+y
            return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
    return None

# --------------------------------------------------------
#  Slash 指令：/setupverifybutton   （僅管理員可用）
# --------------------------------------------------------
@tree.command(name="setupverifybutton",
              description="送出年齡驗證按鈕訊息（管理員）")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify_button(inter: Interaction):
    await send_verify_button(inter.channel)
    await inter.response.send_message("✅ 已送出驗證訊息！", ephemeral=True)

@setup_verify_button.error
async def on_setup_error(inter: Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await inter.response.send_message("❌ 只有管理員能用這個指令喔！", ephemeral=True)
    else:
        raise error

# --------------------------------------------------------
#  UI：驗證按鈕
# --------------------------------------------------------
class VerifyButton(ui.Button):
    def __init__(self):
        super().__init__(label="✅ 我想驗證年齡", style=discord.ButtonStyle.success)

    async def callback(self, inter: Interaction):
        await start_verification(inter)

class VerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(VerifyButton())

async def send_verify_button(channel: discord.abc.Messageable):
    embed = Embed(
        title="歡迎來到本伺服器！",
        description=(
            "請點擊下方 **按鈕** 進行年齡驗證以解鎖更多頻道：\n"
            "‧ 只需上傳 **僅顯示『出生年月日』** 的證件照（請遮蔽其他資料）。\n"
            "‧ 若 AI 無法辨識，可手動輸入生日。"
        ),
        color=0x8B5CF6
    )
    await channel.send(embed=embed, view=VerifyView())

# --------------------------------------------------------
#  驗證流程
# --------------------------------------------------------
async def start_verification(inter: Interaction):

    guild   : discord.Guild  = bot.get_guild(GUILD_ID)
    member  : discord.Member = guild.get_member(inter.user.id)
    role    : discord.Role   = guild.get_role(ROLE_ID)

    # -- 已驗證
    if role in member.roles:
        await inter.response.send_message("你已完成驗證囉！", ephemeral=True)
        return

    # -- 建私密頻道
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member           : discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                       read_messages=True, attach_files=True),
        guild.me         : discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                       manage_channels=True)
    }
    channel = await guild.create_text_channel(
        name=f"verify-{member.name}-{member.discriminator}",
        overwrites=overwrites,
        reason="年齡驗證"
    )

    await inter.response.send_message("📩 已私訊你一個驗證頻道，請查看！", ephemeral=True)

    # 初始指示
    await channel.send(
        f"👋 哈囉 {member.mention}！\n"
        "📸 請上傳 **僅顯示『出生年月日』** 的證件照片（例如身分證背面，**請遮蓋其他個資**）。\n"
        "你有 **10 分鐘** 的時間上傳。"
    )

    # 等附件
    try:
        img_msg: discord.Message = await bot.wait_for(
            "message",
            timeout=600,
            check=lambda m: m.channel == channel and m.author == member and m.attachments
        )
    except asyncio.TimeoutError:
        await channel.send("⌛️ 逾時未收到圖片，請重新點擊驗證按鈕。")
        return

    await channel.send("⏳ 圖片收到，AI 辨識中，請稍候...")

    img_bytes = await img_msg.attachments[0].read()
    birth_str = await ocr_birthdate(img_bytes)

    # -- AI 失敗，改手動輸入
    if birth_str is None:
        await channel.send(
            "⚠️ AI 無法辨識出生日期。\n"
            "⌨️ 請手動輸入你的出生年月日 (格式：YYYY/MM/DD 或 YYYY-MM-DD，例如 2000/01/01)。\n"
            "你有 **5 分鐘** 的時間輸入。"
        )
        try:
            msg: discord.Message = await bot.wait_for(
                "message",
                timeout=300,
                check=lambda m: m.channel == channel and m.author == member
            )
            birth_str = msg.content.strip().replace("/", "-")
        except asyncio.TimeoutError:
            await channel.send("⌛️ 逾時未輸入，請重新開始驗證。")
            return

    # -- 計算年齡
    try:
        birth = datetime.datetime.strptime(birth_str, "%Y-%m-%d").date()
    except ValueError:
        await channel.send("❌ 日期格式錯誤，請重新開始驗證。")
        return

    today = datetime.date.today()
    age   = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

    if age < 18:
        await channel.send(f"🚫 你目前 {age} 歲，未滿 18 歲，無法通過驗證。")
        return

    # -- 通過
    await channel.send(
        f"✅ AI 辨識成功！你的生日是 **{birth_str}**，已滿 **{age}** 歲。\n"
        "正在為你加上身份組..."
    )
    await member.add_roles(role, reason="年齡驗證通過")
    await channel.send("🎉 驗證完成！此頻道將於 15 秒後自動刪除。")

    await asyncio.sleep(15)
    try:
        await channel.delete(reason="驗證完成 (自動刪除)")
    except Exception:
        pass

# --------------------------------------------------------
#  Bot ready → 同步 Slash 指令
# --------------------------------------------------------
@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user} (ID {bot.user.id})")
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    logging.info("✅ Slash commands synced.")

# --------------------------------------------------------
#  Keep-alive 小 Flask（Render 需綁 8080）
# --------------------------------------------------------
app = Flask(__name__)

@app.route("/")
def root():
    return "Bot is running!", 200

def run_keep_alive():
    serve(app, host="0.0.0.0", port=8080)

threading.Thread(target=run_keep_alive, daemon=True).start()

# --------------------------------------------------------
bot.run(BOT_TOKEN)
###########################################################
