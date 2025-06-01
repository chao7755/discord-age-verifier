# Discord 年齡驗證 Bot（Render 免費方案可用版 + EasyOCR）
import os, re, asyncio, logging, textwrap, tempfile, urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path

import discord
from discord import app_commands, ui, Permissions, Intents
from discord.ext import commands, tasks

# ---------- 變數 ----------
TOKEN   = os.getenv("BOT_TOKEN")
GUILD_ID= int(os.getenv("GUILD_ID", 0))
ROLE_ID = 1375827130063126538          # 「成年妹寶」角色 ID
PORT    = int(os.getenv("PORT", 8080))

if not (TOKEN and GUILD_ID and ROLE_ID):
    raise SystemExit("請設定 BOT_TOKEN、GUILD_ID、VERIFIED_ROLE_ID！")

# ---------- 日誌 ----------
logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s:%(name)s: %(message)s")
log = logging.getLogger("verify-bot")

# ---------- Discord ----------
intents = Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Flask keep-alive ----------
from flask import Flask
from waitress import serve
flask_app = Flask(__name__)
@flask_app.route("/")
def index(): return "ok"
asyncio.get_event_loop().run_in_executor(
    None, lambda: serve(flask_app, host="0.0.0.0", port=PORT, _quiet=True)
)

# ---------- 小工具 ----------
date_re = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
def calc_age(b: date):
    today = date.today()
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))

async def parse_date_from_text(txt: str):
    m = date_re.search(txt)
    if not m: return None
    try:
        y, mth, d = map(int, m.groups())
        return date(y, mth, d)
    except ValueError:
        return None

# ---------- EasyOCR （延遲載入）----------
reader = None
async def detect_birthdate(attachment: discord.Attachment) -> date | None:
    """下載圖片 ➜ OCR ➜ 回傳日期或 None"""
    global reader
    # 1. 下載到暫存檔
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(attachment.filename).suffix) as tmp:
        await attachment.save(tmp.name)
        img_path = tmp.name

    # 2. 延遲載入 EasyOCR，第一次會下載模型（~150 MB）
    if reader is None:
        import easyocr
        reader = easyocr.Reader(["ch_tra", "en"], gpu=False)

    # 3. OCR
    result = reader.readtext(img_path, detail=0, paragraph=True)
    text = " ".join(result)
    log.info("OCR text: %s", text[:80])

    # 4. 找日期
    return await parse_date_from_text(text)

# ---------- 驗證按鈕 ----------
class VerifyButton(ui.Button):
    def __init__(self):
        super().__init__(label="🔞點我開始年齡驗證", style=discord.ButtonStyle.success)

    async def callback(self, inter: discord.Interaction):
        member, guild = inter.user, inter.guild
        verified_role = guild.get_role(ROLE_ID)

        if verified_role in member.roles:
            return await inter.response.send_message("你已經是黃黃的妹寶啦！再點都不會更黃", ephemeral=True)

        # 建立私密頻道
        overwrites = {
            guild.default_role: Permissions(view_channel=False),
            member:             Permissions(view_channel=True, send_messages=True, attach_files=True),
            guild.me:           Permissions(view_channel=True, send_messages=True, manage_channels=True)
        }
        channel = await guild.create_text_channel(
            f"verify-{member.display_name}", overwrites=overwrites, reason="年齡驗證"
        )

        # 引導文字
        guide = textwrap.dedent(f"""\
        👋 哈囉 {member.mention}！
        📸 請上傳 **僅顯示『出生年月日』** 的證件照片（例如身分證背面，**請遮蓋其他個資**）。
        你有 10 分鐘的時間上傳。
        """)
        await channel.send(guide)
        await inter.response.send_message(f"已開啟私密頻道 {channel.mention}，請依指示完成驗證！", ephemeral=True)

        def chk(m: discord.Message): return m.channel == channel and m.author == member
        try:
            first_msg = await bot.wait_for("message", check=chk, timeout=600)
        except asyncio.TimeoutError:
            await channel.send("⏰ 逾時 10 分鐘未收到資料，頻道將於 15 秒後關閉。")
            await asyncio.sleep(15)
            return await channel.delete()

        birth: date | None = None

        # ── 有圖片先走 OCR ─────────────────────────
        if first_msg.attachments:
            await channel.send("⏳ 圖片收到，AI 辨識中，請稍候...")
            try:
                birth = await detect_birthdate(first_msg.attachments[0])
            except Exception as e:
                log.warning("OCR 失敗：%s", e)

            if birth is None:
                await channel.send(
                    "⚠️ AI 無法辨識出生日期。\n"
                    "⌨️ 請手動輸入你的出生年月日 (YYYY/MM/DD 或 YYYY-MM-DD)，5 分鐘內有效。"
                )
                try:
                    manual = await bot.wait_for("message", check=chk, timeout=300)
                    birth = await parse_date_from_text(manual.content)
                except asyncio.TimeoutError:
                    birth = None

        # ── 純文字直接解析 ────────────────────────
        else:
            birth = await parse_date_from_text(first_msg.content)

        # ---------- 判定 ----------
        if birth and calc_age(birth) >= 18:
            age = calc_age(birth)
            await channel.send(
                f"✅ AI 辨識成功！你的生日是 {birth:%Y/%m/%d}，已滿 {age} 歲。\n正在為你加上身份組..."
            )
            await member.add_roles(verified_role, reason="通過年齡驗證")
            await asyncio.sleep(5)
            await channel.send("🎉 驗證完成！頻道將於 15 秒後關閉。")
        else:
            await channel.send("❌ 驗證失敗或未滿 18 歲，頻道將於 15 秒後關閉。")

        await asyncio.sleep(15)
        await channel.delete()

# ---------- 管理員指令 ----------
@bot.tree.command(name="setupverifybutton", description="送出年齡驗證按鈕")
@app_commands.checks.has_permissions(administrator=True)
async def setup(inter: discord.Interaction):
    view = ui.View()
    view.add_item(VerifyButton())
    await inter.response.send_message(
        "歡迎來到本伺服器！請點擊下方按鈕進行年齡驗證以解鎖更多頻道：",
        view=view
    )

# ---------- 上線 ----------
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID {bot.user.id})")
    await bot.tree.sync(guild=discord.Object(GUILD_ID))
bot.run(TOKEN)
