# ---------- main.py -------------
import os, re, asyncio, logging, traceback

import discord
from discord import app_commands, Interaction, File
from discord.ext import commands, tasks
print("DEBUG: BOT_TOKEN =", os.getenv("BOT_TOKEN"))
print("DEBUG: GUILD_ID  =", os.getenv("GUILD_ID"))

# ===== 基本設定（從環境變數讀） =====
BOT_TOKEN            = os.getenv("BOT_TOKEN")             # Discord Bot token
GUILD_ID             = int(os.getenv("GUILD_ID", "0"))    # 你的伺服器 ID
VERIFIED_ROLE_NAME   = os.getenv("VERIFIED_ROLE_NAME", "Verified")  # 通過後要給的角色
AGE_LIMIT            = int(os.getenv("AGE_LIMIT", "18"))  # 要滿幾歲

# ===== Discord Intents 與 Bot =====
intents              = discord.Intents.default()
intents.message_content = True
intents.members      = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 懶載入 EasyOCR =====
_ocr_reader = None           # 全域緩存
async def get_reader():
    """
    第一次呼叫時才真正下載 / 初始化 EasyOCR。
    之後直接回傳快取。
    """
    global _ocr_reader
    if _ocr_reader is None:
        loop = asyncio.get_running_loop()
        # 避免阻塞 event-loop，用執行緒載入
        def _load():
            import easyocr   # 延遲 import
            return easyocr.Reader(['ch_tra', 'en'], gpu=False)
        _ocr_reader = await loop.run_in_executor(None, _load)
    return _ocr_reader

# ===== 小工具 =====
async def give_verified_role(member: discord.Member):
    role = discord.utils.get(member.guild.roles, name=VERIFIED_ROLE_NAME)
    # 若角色不存在就自動建立（限 manage_roles 權限）
    if role is None:
        role = await member.guild.create_role(name=VERIFIED_ROLE_NAME,
                                              mentionable=False,
                                              reason="Auto-create verification role")
    await member.add_roles(role, reason="Passed age verification")

def extract_age(text:str) -> int|None:
    """
    從 OCR or 使用者輸入的文字抓出第一個 2-3 位數字，回傳 int。
    """
    m = re.search(r'(\d{2,3})', text)
    return int(m.group(1)) if m else None

# ===== /setupverifybutton 指令 =====
@tree.command(name="setupverifybutton", description="在此頻道發送年齡驗證按鈕")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setup_button(inter: Interaction):
    if not inter.permissions.manage_roles:
        await inter.response.send_message("需要 Manage Roles 權限", ephemeral=True)
        return

    class VerifyButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="✅ 我想驗證年齡", style=discord.ButtonStyle.success)

        async def callback(self, button_inter: Interaction):
            await start_verification(button_inter)

    view = discord.ui.View(timeout=None)
    view.add_item(VerifyButton())
    await inter.response.send_message(
        embed=discord.Embed(
            title="年齡驗證",
            description=f"點擊下方按鈕，按指示上傳身份圖片或手動輸入年齡。"
        ),
        view=view
    )

# ===== 核心：啟動驗證流程 =====
async def start_verification(button_inter: Interaction):
    user = button_inter.user
    guild = button_inter.guild

    # 1) 建立私人子頻道 (private text channel)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user:                 discord.PermissionOverwrite(read_messages=True,
                                                          send_messages=True,
                                                          attach_files=True)
    }
    channel = await guild.create_text_channel(
        name=f"verify-{user.name}",
        overwrites=overwrites,
        reason="Age verification session",
        topic="此頻道將在 5 分鐘後自動刪除"
    )

    await button_inter.response.send_message(
        f"{user.mention} 已為你開啟私人頻道 {channel.mention}，請依指示操作！",
        ephemeral=True
    )

    await channel.send(
        f"{user.mention} 請上傳包含出生日期的圖片 **或** 直接輸入你的年齡（數字）\n"
        f"> ⚠️ 5 分鐘未完成會自動取消"
    )

    def check(m: discord.Message):
        return m.channel == channel and m.author == user

    try:
        # 2) 5 分鐘內等待訊息／圖片
        msg: discord.Message = await bot.wait_for("message", timeout=300, check=check)

        # --- 有附件：跑 OCR ---
        age = None
        if msg.attachments:
            await channel.send("收到圖片，解析中…")
            img_bytes = await msg.attachments[0].read()
            reader = await get_reader()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, reader.readtext, img_bytes, detail=0)
            ocr_text = " ".join(result)
            age = extract_age(ocr_text)

        # --- 純文字：直接取 ---
        if age is None:
            age = extract_age(msg.content)

        # 3) 評估
        if age is None:
            await channel.send("❌ 無法辨識出年齡，請重新開始流程。")
        elif age >= AGE_LIMIT:
            await channel.send(f"✅ 驗證成功！檢測到年齡 **{age}**")
            await give_verified_role(user)
        else:
            await channel.send(f"❌ 你只有 **{age}** 歲，未達 {AGE_LIMIT} 歲限制。")

    except asyncio.TimeoutError:
        await channel.send("⌛ 超過 5 分鐘未回覆，驗證取消。")

    except Exception as e:
        await channel.send("系統錯誤，請聯絡管理員。")
        logging.error(traceback.format_exc())

    finally:
        # 4) 15 秒後自動刪除臨時頻道
        await asyncio.sleep(15)
        try:
            await channel.delete(reason="Verification finished / timeout")
        except Exception:
            pass

# ===== keep-alive：給 Render ping 用 =====
# （Render 免費版閒置 15 分鐘會睡；若你用 UptimeRobot 之類輪詢，可以保持醒著）
from flask import Flask
flask_app = Flask("keep_alive")
@flask_app.route("/")
def home(): return "OK", 200
def run_flask():
    from waitress import serve
    serve(flask_app, host="0.0.0.0", port=8080)

@tasks.loop(count=1)
async def start_keep_alive():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_flask)

# ===== on_ready：同步指令並啟動 Flask =====
@bot.event
async def on_ready():
    print(f"Bot 上線：{bot.user} (id={bot.user.id})")
    try:
        synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔃 Slash commands synced ({len(synced)})")
    except Exception:
        print("⚠️ 指令同步失敗，可忽略（通常是已同步）")
    start_keep_alive.start()

# ======== MAIN ========
if __name__ == "__main__":
    if not BOT_TOKEN or not GUILD_ID:
        raise SystemExit("請設定 BOT_TOKEN 與 GUILD_ID 環境變數！")
    logging.basicConfig(level=logging.INFO)
    bot.run(BOT_TOKEN)
# -------- end main.py ----------
