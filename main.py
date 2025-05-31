import os, re, uuid, asyncio
import cv2
import numpy as np
import pytesseract
import discord
from datetime import datetime
from discord.ext import commands

# ── Flask 保活 ──
from flask import Flask
from threading import Thread

app = Flask('')
@app.route('/')
def home():
    return "Bot is running!"
def run():
    app.run(host='0.0.0.0', port=8080)
Thread(target=run).start()

# ── 基本設定 ──
GUILD_ID = 1364979218697687100  # 換成你的伺服器 ID
VERIFIED_ROLE_NAME = "成年妹寶"
MINIMUM_AGE = 18

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# ── OCR 強化版本 ──
def ocr_date_bytes(image_bytes: bytes):
    try:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        gray = clahe.apply(gray)
        gray = cv2.medianBlur(gray, 3)
        kernel_sharp = np.array([[0,-1,0], [-1,5,-1], [0,-1,0]])
        gray = cv2.filter2D(gray, -1, kernel_sharp)

        binary = cv2.adaptiveThreshold(gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 11, 2)

        h, w = binary.shape
        mask = np.ones_like(binary) * 255
        mask[:int(h * 0.4), :] = 0  # 遮掉可能的中文字
        final = cv2.bitwise_or(binary, mask)

        config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789/-'
        raw_text = pytesseract.image_to_string(final, lang='eng', config=config)

        date_patterns = [
            r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b",
            r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"
        ]
        for pattern in date_patterns:
            match = re.search(pattern, raw_text)
            if match:
                if len(match.group(1)) == 4:
                    y, m, d = match.group(1), match.group(2), match.group(3)
                else:
                    d, m, y = match.group(1), match.group(2), match.group(3)
                return raw_text, f"{y}-{m}-{d}"
        return raw_text, ""
    except Exception as e:
        print("OCR 錯誤:", e)
        return "", ""

# ── 計算年齡 ──
def parse_birthdate_to_age(date_str: str):
    if not date_str: return None, None
    ds = date_str.replace('/', '-').replace('.', '-')
    m1 = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", ds)
    m2 = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", ds)
    try:
        if m1:
            y, m, d = map(int, m1.groups())
        elif m2:
            d, m, y = map(int, m2.groups())
        else:
            return None, None
        bd = datetime(y, m, d)
        today = datetime.now()
        age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        return age, f"{y:04d}-{m:02d}-{d:02d}"
    except:
        return None, None

# ── 驗證流程 ──
async def start_verify_flow(guild, member, source):
    role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if role and role in member.roles:
        await _reply(source, "✅ 你已通過驗證")
        return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    }
    cname = f"verify-{member.name}-{uuid.uuid4().hex[:6]}"
    try:
        chan = await guild.create_text_channel(cname, overwrites=overwrites)
        await _reply(source, f"✅ 建立驗證頻道：{chan.mention}")
    except:
        await _reply(source, "❌ 建立頻道失敗，請檢查權限")
        return

    await chan.send(
        f"{member.mention} 請上傳包含出生日期的證件照片（遮住其他資訊）。你有 10 分鐘。\n"
        "🔍 圖像務必清晰，建議裁切僅保留日期區域。")

    def image_check(msg):
        return msg.author == member and msg.channel == chan and msg.attachments

    age_ok = False
    try:
        msg = await bot.wait_for("message", timeout=600, check=image_check)
        await chan.send("⏳ 處理中…")
        img_bytes = await msg.attachments[0].read()
        raw, date = ocr_date_bytes(img_bytes)
        age, fmt = parse_birthdate_to_age(date)
        if age is not None and age >= MINIMUM_AGE:
            await chan.send(f"✅ 生日：{fmt}，年齡：{age} 歲。賦予身份組中…")
            age_ok = True
        elif age:
            await chan.send(f"❌ 未滿 {MINIMUM_AGE} 歲（生日：{fmt}）")
        else:
            await chan.send("⚠️ 無法正確辨識日期，請手動輸入 YYYY-MM-DD。")
    except asyncio.TimeoutError:
        await chan.send("⌛ 上傳逾時")

    if not age_ok:
        await chan.send("請手動輸入出生日期（YYYY-MM-DD），5 分鐘內有效")
        def text_check(m): return m.author == member and m.channel == chan
        try:
            msg = await bot.wait_for("message", timeout=300, check=text_check)
            age, fmt = parse_birthdate_to_age(msg.content)
            if age and age >= MINIMUM_AGE:
                await chan.send(f"✅ 手動確認生日：{fmt}，年齡 {age} 歲。賦予身份組中…")
                age_ok = True
            else:
                await chan.send("❌ 年齡不足或格式錯誤。")
        except asyncio.TimeoutError:
            await chan.send("⌛ 未收到輸入。")

    if age_ok and role:
        try:
            await member.add_roles(role)
            await chan.send(f"🎉 身份組 `{VERIFIED_ROLE_NAME}` 已授予！")
        except:
            await chan.send("⚠️ 加身份組失敗，請通知管理員")

    await chan.send("頻道將在 60 秒後刪除")
    await asyncio.sleep(60)
    try: await chan.delete()
    except: pass

async def _reply(source, text):
    if isinstance(source, discord.Interaction):
        if not source.response.is_done():
            await source.response.send_message(text, ephemeral=True)
        else:
            await source.followup.send(text, ephemeral=True)
    else:
        await source.send(text)

# ── /verify 指令 ──
@bot.tree.command(name="verify", description="開始年齡驗證")
async def verify_slash(interaction: discord.Interaction):
    asyncio.create_task(start_verify_flow(interaction.guild, interaction.user, interaction))

# ── 按鈕 View ──
class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🔞 點我開始年齡驗證", style=discord.ButtonStyle.primary, custom_id="verify_button")
    async def button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("正在準備驗證頻道…", ephemeral=True)
        asyncio.create_task(start_verify_flow(interaction.guild, interaction.user, interaction.followup))

# ── 管理員指令：建立按鈕 ──
@bot.command(name="setupverifybutton")
@commands.has_permissions(administrator=True)
async def setup_verify_button(ctx):
    await ctx.send("請點擊下方按鈕開始年齡驗證：", view=VerificationView())
    await ctx.message.delete()

# ── Bot 啟動時 ──
@bot.event
async def on_ready():
    print(f"Bot 上線：{bot.user}")
    bot.add_view(VerificationView())
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)

# ── 啟動 BOT ──
if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("❌ BOT_TOKEN 環境變數未設定")
    else:
        bot.run(TOKEN)
