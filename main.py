###########################################################
# main.py   (Render Free 512 MB OK) ── 2025-06-17 版本
# -- 僅做必需修改；其餘完整保留你原先的程式碼 --
###########################################################
import os, re, io, asyncio, datetime, logging, threading

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction, Embed

from PIL import Image
import requests

from flask import Flask
from waitress import serve


# ────────── 基本設定 ──────────
logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s:%(name)s: %(message)s")

BOT_TOKEN        = os.getenv("BOT_TOKEN")
GUILD_ID         = int(os.getenv("GUILD_ID", 0))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", 0))

# **雲端 OCR**
OCR_API_KEY      = os.getenv("OCR_API_KEY")           # 你在 Render 後台填的
OCR_ENDPOINT     = "https://api.ocr.space/parse/image"

if not all([BOT_TOKEN, GUILD_ID, VERIFIED_ROLE_ID, OCR_API_KEY]):
    raise RuntimeError("❌ 環境變數未填齊！BOT_TOKEN / GUILD_ID / VERIFIED_ROLE_ID / OCR_API_KEY")


# ────────── Intents ──────────
intents = discord.Intents.default()
intents.members          = True
intents.message_content  = True


# ────────── Bot (改：使用自訂 Bot 類 + setup_hook 做 slash 同步) ──────────
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        """Render 開機時先行同步指令到指定 guild──立刻可見。"""
        guild_obj = discord.Object(id=GUILD_ID)

        # 若想保留全域指令可刪除此行
        self.tree.copy_global_to_guild(guild_obj)

        await self.tree.sync(guild=guild_obj)
        logging.info("Slash commands synced to guild %s", GUILD_ID)


bot  = MyBot()
tree = bot.tree



# ---------------------------------------------------------
#  Slash：/setupverifybutton
#  (改：直接鎖定特定 guild，省去 copy_global_to_guild 亦可)
# ---------------------------------------------------------
@tree.command(
    name="setupverifybutton",
    description="（管理員）送出驗證按鈕訊息",
    guild=discord.Object(id=GUILD_ID)          # ← ★ 新增：僅在目標伺服器註冊
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify_button(inter: Interaction):
    await send_verify_button(inter.channel)
    await inter.response.send_message("✅ 已送出驗證訊息！", ephemeral=True)


@setup_verify_button.error
async def on_setup_error(inter, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await inter.response.send_message("❌ 只有管理員能用這個指令喔！", ephemeral=True)
    else:
        raise error



# ---------------------------------------------------------
#  UI：按鈕
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


async def send_verify_button(channel):
    embed = Embed(
        title="歡迎來到本伺服器！",
        description=(
            "請點擊下方按鈕進行 **年齡驗證** 以解鎖更多頻道：\n"
            "・只需上傳 **僅顯示『出生年月日』** 的證件照（請遮擋其他資料）。\n"
            "・AI 無法辨識時，可手動輸入生日。"
        ),
        color=0x8B5CF6
    )
    await channel.send(embed=embed, view=VerifyView())



# ---------------------------------------------------------
#  驗證流程（原樣保留）
# ---------------------------------------------------------
async def start_verification(inter: Interaction):

    guild   : discord.Guild  = bot.get_guild(GUILD_ID)
    member  : discord.Member = guild.get_member(inter.user.id)
    role    : discord.Role   = guild.get_role(VERIFIED_ROLE_ID)

    if role in member.roles:
        await inter.response.send_message("你已經是黃黃的妹寶啦！再點都不會更黃^^", ephemeral=True)
        return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member           : discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        guild.me         : discord.PermissionOverwrite(view_channel=True,  send_messages=True, manage_channels=True)
    }
    channel = await guild.create_text_channel(
        name=f"verify-{member.name}-{member.discriminator}",
        overwrites=overwrites,
        reason="年齡驗證"
    )

    await inter.response.send_message("📩 已開啟私密驗證頻道，請點擊！", ephemeral=True)

    await channel.send(
        f"👋 哈囉 {member.mention}！\n"
        "📸 請上傳 **僅顯示『出生年月日』** 的證件照片（例如身分證背面，**請遮蓋其他個資**）。\n"
        "你有 **10 分鐘** 的時間上傳。"
    )

    # -- 等圖片 --
    def img_ok(m: discord.Message):
        return m.channel == channel and m.author == member and m.attachments

    try:
        img_msg = await bot.wait_for("message", timeout=600, check=img_ok)
    except asyncio.TimeoutError:
        await channel.send("⌛️ 等待逾時，請重新點擊驗證按鈕開始。")
        return

    await channel.send("⏳ 圖片收到，AI 辨識中，請稍候...")

    # ---------- 呼叫雲端 OCR ----------
    img_bytes = await img_msg.attachments[0].read()

    # 先轉成 jpg，避免部分 API 不收 HEIC / webp
    img_pil   = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    buf       = io.BytesIO()
    img_pil.save(buf, format="JPEG", quality=95)
    buf.seek(0)

    resp = requests.post(
        OCR_ENDPOINT,
        files={"file": ("image.jpg", buf, "image/jpeg")},
        data={"apikey": OCR_API_KEY, "language": "eng"},
        timeout=60
    )
    try:
        parsed = resp.json()
        text   = parsed["ParsedResults"][0]["ParsedText"]
    except Exception:
        text = ""

    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    birthdate_str = None
    if m:
        birthdate_str = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # ---------- AI 失敗 → 手動輸入 ----------
    if birthdate_str is None:
        await channel.send(
            "⚠️ AI 無法辨識出生日期。\n"
            "⌨️ 請手動輸入你的出生年月日 (格式：YYYY/MM/DD 或 YYYY-MM-DD，例如 2000/01/01)。\n"
            "你有 **5 分鐘** 的時間輸入。"
        )

        def date_ok(m):
            return m.channel == channel and m.author == member

        try:
            msg = await bot.wait_for("message", timeout=300, check=date_ok)
            birthdate_str = msg.content.strip().replace("/", "-")
        except asyncio.TimeoutError:
            await channel.send("⌛️ 逾時未輸入，請重新開始驗證。")
            return

    # ---------- 計算年齡 ----------
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

    # ---------- 通過 ----------
    await channel.send(
        f"✅ AI 辨識成功！你的生日是 **{birthdate_str}**，已滿 **{age}** 歲。\n"
        "正在為你加上身份組..."
    )
    await member.add_roles(role, reason="年齡驗證通過")
    await channel.send("🎉 驗證完成！此頻道將於 15 秒後自動刪除。")

    await asyncio.sleep(15)
    await channel.delete(reason="驗證完成 (自動刪除)")



# ---------------------------------------------------------
#  Bot 連線 / 日誌
# ---------------------------------------------------------
@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID {bot.user.id})")



# ────────── keep-alive （改：Render 用動態 $PORT） ──────────
app = Flask("alive")

@app.route("/")
def ok():
    return "Bot is running!", 200

threading.Thread(
    target=lambda: serve(app,
                         host="0.0.0.0",
                         port=int(os.getenv("PORT", 8080))),  # ← ★ 改用環境變數 PORT
    daemon=True
).start()



# ────────── Go! ──────────
bot.run(BOT_TOKEN)
###########################################################
