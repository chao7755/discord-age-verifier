########################  main.py  ########################
import os, re, asyncio, datetime, logging, io, threading

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction, Embed

import easyocr
import numpy as np
import cv2                                         # opencv-python

from flask import Flask
from waitress import serve

# ---------- 基本設定 ----------
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

BOT_TOKEN        = os.getenv("BOT_TOKEN")
GUILD_ID         = int(os.getenv("GUILD_ID", 0))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", 0))          # 「成年妹寶」角色 ID

if not all([BOT_TOKEN, GUILD_ID, VERIFIED_ROLE_ID]):
    raise RuntimeError("❌ 請在 Render 的 Environment 變數填好 BOT_TOKEN / GUILD_ID / VERIFIED_ROLE_ID！")

# ---------- Intents ----------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # 後台也要勾選 Message Content Intent

# ---------- Bot & Slash 指令管理器 ----------
bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree                               # 一定要先宣告，後面 decorator 才找得到

# ---------- OCR ----------
reader = easyocr.Reader(['en', 'ch_tra'], gpu=False)

# ---------------------------------------------------------
#  Slash 指令：/setupverifybutton
# ---------------------------------------------------------
@tree.command(
    name        = "setupverifybutton",
    description = "（管理員）送出驗證按鈕訊息"
)
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


# ---------------------------------------------------------
#  UI：驗證按鈕
# ---------------------------------------------------------
class VerifyButton(ui.Button):
    def __init__(self):
        super().__init__(label="🔞點我開始年齡驗證", style=discord.ButtonStyle.success)

    async def callback(self, inter: Interaction):
        await start_verification(inter)


class VerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(VerifyButton())


async def send_verify_button(channel: discord.abc.Messageable):
    embed = Embed(
        title       = "歡迎來到本伺服器！",
        description = (
            "請點擊下方按鈕進行 **年齡驗證** 以解鎖更多頻道：\n"
            "・只需上傳 **僅顯示『出生年月日』** 的證件照（請遮擋其他資料）。\n"
            "・AI 無法辨識時，可手動輸入生日。"
        ),
        color=0x8B5CF6
    )
    await channel.send(embed=embed, view=VerifyView())


# ---------------------------------------------------------
#  驗證流程
# ---------------------------------------------------------
async def start_verification(inter: Interaction):

    guild   : discord.Guild  = bot.get_guild(GUILD_ID)
    member  : discord.Member = guild.get_member(inter.user.id)
    role    : discord.Role   = guild.get_role(VERIFIED_ROLE_ID)

    # 已驗證過
    if role in member.roles:
        await inter.response.send_message("你已經是黃黃的妹寶啦！再點都不會更黃^^", ephemeral=True)
        return

    # 建一個私密頻道
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member           : discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True),
        guild.me         : discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, manage_channels=True)
    }
    channel = await guild.create_text_channel(
        name=f"verify-{member.name}-{member.discriminator}",
        overwrites=overwrites,
        reason="年齡驗證"
    )

    await inter.response.send_message("📩 已開啟私密驗證頻道，請點擊！", ephemeral=True)

    # 初始訊息
    await channel.send(
        f"👋 哈囉 {member.mention}！\n"
        "📸 請上傳 **僅顯示『出生年月日』** 的證件照片（例如身分證背面，**請遮蓋其他個資**）。\n"
        "你有 **10 分鐘** 的時間上傳。"
    )

    # ----------- 等圖片 -----------
    def image_check(m: discord.Message):
        return m.channel == channel and m.author == member and m.attachments

    birthdate_str = None
    try:
        img_msg: discord.Message = await bot.wait_for("message", timeout=600, check=image_check)
    except asyncio.TimeoutError:
        await channel.send("⌛️ 等待逾時，請重新點擊驗證按鈕開始。")
        return

    await channel.send("⏳ 圖片收到，AI 辨識中，請稍候...")

    # 讀取圖片至 numpy
    img_bytes = await img_msg.attachments[0].read()
    img_np    = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)

    # OCR
    text_lines = reader.readtext(img_np, detail=0)
    text_all   = " ".join(text_lines)
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text_all)
    if m:
        birthdate_str = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # ----------- AI 失敗 → 手動輸入 -----------
    if birthdate_str is None:
        await channel.send(
            "⚠️ AI 無法辨識出生日期。\n"
            "⌨️ 請手動輸入你的出生年月日 (格式：YYYY/MM/DD 或 YYYY-MM-DD，例如 2000/01/01)。\n"
            "你有 **5 分鐘** 的時間輸入。"
        )

        def date_check(m: discord.Message):
            return m.channel == channel and m.author == member

        try:
            msg: discord.Message = await bot.wait_for("message", timeout=300, check=date_check)
            birthdate_str = msg.content.strip().replace("/", "-")
        except asyncio.TimeoutError:
            await channel.send("⌛️ 逾時未輸入，請重新開始驗證。")
            return

    # ----------- 計算年齡 -----------
    try:
        birthdate = datetime.datetime.strptime(birthdate_str, "%Y-%m-%d").date()
    except ValueError:
        await channel.send("❌ 日期格式錯誤，驗證失敗，請重新開始。")
        return

    today = datetime.date.today()
    age   = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))

    if age < 18:
        await channel.send(f"🚫 你目前 {age} 歲，未滿 18 歲，無法通過驗證。")
        return

    # ----------- 通過 -----------
    await channel.send(
        f"✅ AI 辨識成功！你的生日是 **{birthdate_str}**，已滿 **{age}** 歲。\n"
        "正在為你加上身份組..."
    )
    await member.add_roles(role, reason="年齡驗證通過")
    await channel.send("🎉 驗證完成！此頻道將於 15 秒後自動刪除。")

    # 刪除頻道
    await asyncio.sleep(15)
    try:
        await channel.delete(reason="驗證完成 (自動刪除)")
    except Exception:
        pass


# ---------------------------------------------------------
#  Bot ready → 同步 Slash 指令
# ---------------------------------------------------------
@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID {bot.user.id})")
    guild_obj = discord.Object(id=GUILD_ID)
    await tree.sync(guild=guild_obj)
    logging.info("Slash commands synced.")


# ---------------------------------------------------------
#  Keep-alive: 在 8080 Port 開一個小 Flask
# ---------------------------------------------------------
app = Flask("alive")

@app.route("/")
def ok():
    return "Bot is running!", 200

def run_keep_alive():
    serve(app, host="0.0.0.0", port=8080)

threading.Thread(target=run_keep_alive, daemon=True).start()

# ---------------------------------------------------------
#  Go!
# ---------------------------------------------------------
bot.run(BOT_TOKEN)
###########################################################
