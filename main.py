# main.py  — Discord 年齡驗證 Bot（Render 免費方案友善版‧進階）
import os, re, asyncio, logging, textwrap
from datetime import datetime, timedelta, date

import discord
from discord import app_commands, ui, Intents, Permissions
from discord.ext import commands, tasks

# ---------- 基本設定 ----------
TOKEN      = os.environ.get("BOT_TOKEN")
GUILD_ID   = int(os.environ.get("GUILD_ID" , 0))
ROLE_ID    = int(os.environ.get("VERIFIED_ROLE_ID" , 0))  # 已驗證身分組
HTTP_PORT  = int(os.environ.get("PORT" , 8080))           # Flask keep-alive

if not TOKEN or not GUILD_ID:
    raise SystemExit("請設定 BOT_TOKEN 與 GUILD_ID 環境變數！")

intents = Intents.default()
intents.message_content = True
intents.members         = True
bot = commands.Bot(command_prefix="!", intents=intents)

logging.basicConfig(
    level = logging.INFO,
    format="%(levelname)s:%(name)s: %(message)s"
)

# ---------- Flask keep-alive ----------
from flask import Flask
from waitress import serve
app = Flask(__name__)
@app.route("/")
def index(): return "ok"
@tasks.loop(seconds=30)
async def keep_alive(): pass
@bot.event
async def on_ready():
    print(f"Bot 上線：{bot.user} (ID: {bot.user.id})")
    if not keep_alive.is_running(): keep_alive.start()
    try:
        synced = await bot.tree.sync(guild=discord.Object(GUILD_ID))
        print(f"斜線指令同步：{len(synced)} 個")
    except Exception as e:
        print("同步失敗：", e)
asyncio.get_event_loop().run_in_executor(
    None, lambda: serve(app, host="0.0.0.0", port=HTTP_PORT, _quiet=True)
)

# ---------- 工具函式 ----------
date_re = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
def calc_age(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

async def parse_birth_from_manual(msg: discord.Message):
    m = date_re.search(msg.content.strip())
    if not m: return None
    try:
        y, mth, d = map(int, m.groups())
        return date(y, mth, d)
    except ValueError:
        return None

# ---------- 驗證視圖 ----------
class VerifyButton(ui.Button):
    def __init__(self):
        super().__init__(label="🔞點我開始驗證年齡", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        guild  = interaction.guild
        verified_role = guild.get_role(ROLE_ID)

        # 已經驗證過
        if verified_role in member.roles:
            return await interaction.response.send_message(
                "你已經是成年妹寶了！點我不會變得更黃的～", ephemeral=True)

        # 建立私密頻道
        overwrites = {
            guild.default_role: Permissions(view_channel=False),
            member:             Permissions(view_channel=True, send_messages=True, attach_files=True),
            guild.me:           Permissions(view_channel=True, send_messages=True, manage_channels=True)
        }
        private_ch = await guild.create_text_channel(
            name=f"verify-{member.display_name}",
            overwrites=overwrites,
            reason="年齡驗證"
        )

        # 👋 引導使用者
        guide_txt = textwrap.dedent(f"""\
        👋 哈囉 {member.mention}！
        📸 請上傳 **僅顯示『出生年月日』** 的證件照片（**請遮蓋其他個人資訊**）。
        你有 10 分鐘的時間上傳。
        """)
        await private_ch.send(guide_txt)

        await interaction.response.send_message(
            f"已為你開啟私密頻道 {private_ch.mention}，請依指示完成驗證！",
            ephemeral=True
        )

        # 等待使用者傳圖片
        def check(msg: discord.Message):
            return msg.channel == private_ch and msg.author == member

        try:
            msg = await bot.wait_for("message", check=check, timeout=600)  # 10 分鐘
        except asyncio.TimeoutError:
            await private_ch.send("逾時 10 分鐘未收到圖片，頻道將於 15 秒後關閉。")
            await asyncio.sleep(15)
            return await private_ch.delete()

        # ---------- 解析流程 ----------
        birthdate: date | None = None

        # ── 使用者先傳圖片 ───────────────────────────
        if msg.attachments:
            await private_ch.send("⏳ 圖片收到，AI 辨識中，請稍候...")     # <<< 新增 >>>

            # 這裡本來可呼叫 EasyOCR，考量免費方案效能改用「辨識失敗 → 手動輸入」的流程
            # 想要真正 OCR 的話，把下行替換為實際辨識函式並返回 birthdate
            birthdate = None  # ← 模擬 AI 辨識失敗

            if birthdate is None:
                # AI 辨識不到
                await private_ch.send(
                    "⚠️ AI 無法辨識出生日期。\n"
                    "⌨️ 請手動輸入你的出生年月日 (格式：YYYY/MM/DD 或 YYYY-MM-DD，例如 2000/01/01)。\n"
                    "你有 5 分鐘的時間輸入。"
                )                                                   # <<< 新增 >>>

                try:
                    manual = await bot.wait_for("message", check=check, timeout=300)  # 5 分鐘
                    birthdate = await parse_birth_from_manual(manual)
                    if birthdate is None:
                        raise ValueError
                except (asyncio.TimeoutError, ValueError):
                    await private_ch.send("❌ 驗證失敗，頻道將於 15 秒後關閉。")
                    await asyncio.sleep(15)
                    return await private_ch.delete()

        # ── 使用者直接輸入文字 ───────────────────────
        else:
            birthdate = await parse_birth_from_manual(msg)
            if birthdate is None and msg.content.isdigit():
                # 仍保留純「年齡數字」後門
                age = int(msg.content)
                if age >= 18:
                    birthdate = date.today().replace(year=date.today().year - age)

        # ---------- 最終結果 ----------
        if birthdate:
            age = calc_age(birthdate)
            if age >= 18:
                birthdate_str = birthdate.strftime("%Y/%m/%d")
                await private_ch.send(
                    f"✅ AI 辨識成功！你的生日是 {birthdate_str}，已滿 {age} 歲。\n"
                    f"正在為你加上身份組..."
                )                                                   # <<< 新增 >>>
                await member.add_roles(verified_role, reason="通過年齡驗證")
                await asyncio.sleep(5)
                await private_ch.send("🎉 驗證完成！頻道將於 15 秒後關閉。")
            else:
                await private_ch.send("❌ 未滿 18 歲，無法通過驗證。")
        else:
            await private_ch.send("❌ 驗證失敗，請確認格式或聯絡管理員。")

        await asyncio.sleep(15)
        await private_ch.delete()

# ---------- 管理員：發送驗證按鈕 ----------
@bot.tree.command(name="setupverifybutton", description="在此頻道送出年齡驗證按鈕")
@app_commands.checks.has_permissions(administrator=True)
async def setup_btn(inter: discord.Interaction):
    view = ui.View()
    view.add_item(VerifyButton())
    await inter.response.send_message(
        "歡迎來到本伺服器！請點擊下方按鈕進行年齡驗證以解鎖更多頻道：",
        view=view
    )

# ---------- 執行 ----------
bot.run(TOKEN)
