"""
Discord 年齡驗證機器人（OpenCV + Tesseract 輕量版）
— 適用 Replit 免費額度 —
"""
import os
import re
import uuid
import asyncio
import cv2 # OpenCV
import numpy as np
import discord
from datetime import datetime
from discord.ext import commands
import pytesseract # 移到全域，避免重複 import

# ── 設定區 ───────────────────────────────────────────
# !!! 重要：請將下面的 ID 換成你自己的 Discord 伺服器 ID !!!
GUILD_ID = 1364979218697687100  # ← 🚨 請務必換成你的伺服器 ID (一個很長的數字)

# !!! 重要：請確認你的伺服器有這個身份組，或者修改成你想要的名稱 !!!
VERIFIED_ROLE_NAME = "成年妹寶" # ← 驗證成功後給予的身份組名稱

# 年齡限制
MINIMUM_AGE = 18

# ── Bot 基本設定 ──────────────────────────────────────
intents = discord.Intents.default()
intents.members = True  # 需要成員 intent 才能在加入時操作身份組等
intents.message_content = True # 需要訊息內容 intent 才能讀取使用者訊息
bot = commands.Bot(command_prefix="/", intents=intents) # 指令前綴，這裡用 / 表示主要使用斜線指令

# ── OCR 前處理 + Tesseract ───────────────────────────
def ocr_date_bytes(image_bytes: bytes):
    """
    從圖片的 bytes 資料中進行 OCR 並提取日期。
    """
    try:
        # 將 bytes 轉為 OpenCV 圖片物件 (灰階)
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)

        # 放大圖片，有助於 OCR 辨識 (可調整 fx, fy 的值)
        img = cv2.resize(img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)

        # 圖像增強 (這些步驟可以根據實際圖片效果調整或增刪)
        # 1. 閉運算：填充小的黑色區域，連接白色區域 (試圖移除噪點，連接數字斷裂部分)
        kernel_morph = cv2.getStructuringElement(cv2.MORPH_RECT, (5,5)) # 核心大小可調整
        img = cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel_morph)

        # 2. 適應性閾值處理：將圖像二值化 (黑白)
        img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 11, 2) # block_size 和 C 值可調整

        # 3. (可選) 如果噪點多，可以再做一次膨脹或腐蝕，或者中值濾波
        # img = cv2.medianBlur(img, 3) # 中值濾波去胡椒鹽噪點

        # Tesseract 設定
        # --psm 7: 將圖片視為單行文字。如果日期格式多樣，可嘗試其他 psm 值，如 6 (假設為統一的文字區塊) 或 11 (稀疏文字)。
        # tessedit_char_whitelist: 只辨識這些字元，有助於提高準確率
        config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789/-'
        raw_text = pytesseract.image_to_string(img, lang='eng', config=config) # lang='eng' 因為日期格式常用英文語系數字

        # 正規表達式搜尋日期 (年/月/日 或 月/日/年)
        # 考慮 YYYY/MM/DD, YYYY-MM-DD, YYYY.MM.DD
        # MM/DD/YYYY, MM-DD-YYYY, MM.DD.YYYY
        # DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY (較少見於純數字，但以防萬一)
        date_patterns = [
            r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", # YYYY-MM-DD or YYYY.MM.DD or YYYY MM DD
            r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})"  # MM-DD-YYYY or MM.DD.YYYY or MM DD YYYY
        ]

        extracted_date_str = ""
        for pattern in date_patterns:
            match = re.search(pattern, raw_text)
            if match:
                if len(match.group(1)) == 4: # YYYY at start
                    year, month, day = match.group(1), match.group(2), match.group(3)
                else: # YYYY at end
                    month, day, year = match.group(1), match.group(2), match.group(3)
                extracted_date_str = f"{year}-{month}-{day}" # 標準化格式
                break

        return raw_text, extracted_date_str

    except Exception as e:
        print(f"OCR 處理錯誤: {e}")
        return "", ""

# ── 解析生日 → 年齡 ───────────────────────────────────
def parse_birthdate_to_age(date_str: str):
    """
    解析日期字串並計算年齡。
    接受 YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
    或 MM-DD-YYYY, MM/DD/YYYY, MM.DD.YYYY
    或 DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY (較不推薦，易混淆)
    """
    if not date_str:
        return None, None

    # 標準化分隔符為 '-'
    date_str_normalized = date_str.replace('/', '-').replace('.', '-').replace(' ', '-')

    # 嘗試 YYYY-MM-DD
    match_ymd = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str_normalized)
    if match_ymd:
        try:
            y, m, d = int(match_ymd.group(1)), int(match_ymd.group(2)), int(match_ymd.group(3))
        except ValueError:
            return None, None # 數字轉換失敗
    else:
        # 嘗試 MM-DD-YYYY
        match_mdy = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", date_str_normalized)
        if match_mdy:
            try:
                m, d, y = int(match_mdy.group(1)), int(match_mdy.group(2)), int(match_mdy.group(3))
            except ValueError:
                return None, None
        else:
            # 嘗試 DD-MM-YYYY (如果前面兩種都沒有匹配)
            match_dmy = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", date_str_normalized) # 這裡假設如果不是 YYYY 開頭也不是 YYYY 結尾，而是這種
            if match_dmy:
                 try:
                     d, m, y = int(match_dmy.group(1)), int(match_dmy.group(2)), int(match_dmy.group(3))
                 except ValueError:
                     return None, None
            else:
                 return None, None # 無法識別格式

    try:
        birth_datetime = datetime(y, m, d)
        today = datetime.now()
        age = today.year - birth_datetime.year - ((today.month, today.day) < (birth_datetime.month, birth_datetime.day))
        return age, f"{y:04d}-{m:02d}-{d:02d}" # 回傳標準格式 YYYY-MM-DD
    except ValueError: # 日期無效 (例如 2月30日)
        return None, None

# ── 驗證流程 ─────────────────────────────────────────
async def start_verify_flow(guild: discord.Guild, member: discord.Member, interaction_or_message_channel):
    """
    開始驗證流程，創建私密頻道。
    interaction_or_message_channel: 可以是 Interaction (用於斜線指令回應) 或 Messageable (用於按鈕回應)。
    """
    # 檢查使用者是否已經有驗證過的身份組
    verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if verified_role and verified_role in member.roles:
        if isinstance(interaction_or_message_channel, discord.Interaction):
            await interaction_or_message_channel.response.send_message("✅ 你已經通過驗證了！", ephemeral=True)
        else: # 假設是按鈕點擊後的 interaction.followup or messageable
            await interaction_or_message_channel.send("✅ 你已經通過驗證了！", ephemeral=True if isinstance(interaction_or_message_channel, discord.Interaction) else False)
        return

    # 建立私密頻道
    # 權限設定：預設所有人不可見，該成員和 Bot 可見可發言
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    }

    channel_name = f"verify-{member.name}-{uuid.uuid4().hex[:6]}"
    try:
        verification_channel = await guild.create_text_channel(channel_name, overwrites=overwrites, category=None) # 可指定分類
        if isinstance(interaction_or_message_channel, discord.Interaction) and not interaction_or_message_channel.response.is_done():
            await interaction_or_message_channel.response.send_message(f"✅ 已為你建立專屬驗證頻道：{verification_channel.mention}", ephemeral=True)
        elif hasattr(interaction_or_message_channel, 'send_message') and callable(getattr(interaction_or_message_channel, 'send_message')): # For button interactions, use followup
             await interaction_or_message_channel.send_message(f"✅ 已為你建立專屬驗證頻道：{verification_channel.mention}", ephemeral=True)

    except discord.Forbidden:
        print("錯誤：Bot 沒有權限建立頻道。")
        if isinstance(interaction_or_message_channel, discord.Interaction) and not interaction_or_message_channel.response.is_done():
            await interaction_or_message_channel.response.send_message("❌ Bot 無法建立頻道，請聯絡管理員。", ephemeral=True)
        elif hasattr(interaction_or_message_channel, 'send_message'):
             await interaction_or_message_channel.send_message("❌ Bot 無法建立頻道，請聯絡管理員。", ephemeral=True)
        return
    except Exception as e:
        print(f"建立頻道時發生未知錯誤: {e}")
        if isinstance(interaction_or_message_channel, discord.Interaction) and not interaction_or_message_channel.response.is_done():
            await interaction_or_message_channel.response.send_message("❌ 建立驗證頻道時發生錯誤。", ephemeral=True)
        elif hasattr(interaction_or_message_channel, 'send_message'):
             await interaction_or_message_channel.send_message("❌ 建立驗證頻道時發生錯誤。", ephemeral=True)
        return

    await verification_channel.send(f"👋 哈囉 {member.mention}！\n📸 請上傳 **僅顯示『出生年月日』** 的證件照片（例如身分證背面，**請遮蓋其他個資**）。\n你有 10 分鐘的時間上傳。")

    def check_image_message(msg):
        return msg.author == member and msg.channel == verification_channel and len(msg.attachments) > 0 and msg.attachments[0].content_type.startswith('image/')

    age_verified = False
    try:
        image_message = await bot.wait_for("message", timeout=600.0, check=check_image_message) # 10分鐘
        attachment = image_message.attachments[0]

        await verification_channel.send("⏳ 圖片收到，AI 辨識中，請稍候...")
        image_bytes = await attachment.read()

        raw_ocr_text, extracted_date = ocr_date_bytes(image_bytes)
        # print(f"原始 OCR 文字: {raw_ocr_text}") # Debug 用
        # print(f"提取到的日期: {extracted_date}") # Debug 用

        if extracted_date:
            age, birthdate_str = parse_birthdate_to_age(extracted_date)
            if age is not None and birthdate_str:
                if age >= MINIMUM_AGE:
                    await verification_channel.send(f"✅ AI 辨識成功！你的生日是 {birthdate_str}，已滿 {age} 歲。\n正在為你加上身份組...")
                    age_verified = True
                else:
                    await verification_channel.send(f"⚠️ AI 辨識你的生日是 {birthdate_str}，未滿 {MINIMUM_AGE} 歲。")
            else:
                await verification_channel.send(f"⚠️ AI 似乎提取到日期文字 `{extracted_date}`，但無法成功解析為有效生日。")
        else:
            await verification_channel.send("AI 無法從圖片中辨識出日期。")

    except asyncio.TimeoutError:
        await verification_channel.send("⌛ 上傳圖片超時。")
    except Exception as e:
        await verification_channel.send(f"處理圖片時發生錯誤: {e}")
        print(f"圖片處理/OCR錯誤: {e}")


    if not age_verified: # 如果 AI 辨識失敗或年齡不符
        await verification_channel.send(f"⌨️ 請手動輸入你的出生年月日 (格式：YYYY/MM/DD 或 YYYY-MM-DD，例如 2000/01/01)。\n你有 5 分鐘的時間輸入。")
        def check_text_message(msg):
            return msg.author == member and msg.channel == verification_channel
        try:
            date_message = await bot.wait_for("message", timeout=300.0, check=check_text_message) # 5分鐘
            age, birthdate_str = parse_birthdate_to_age(date_message.content)
            if age is not None and birthdate_str:
                if age >= MINIMUM_AGE:
                    await verification_channel.send(f"✅ 手動輸入確認！你的生日是 {birthdate_str}，已滿 {age} 歲。\n正在為你加上身份組...")
                    age_verified = True
                else:
                    await verification_channel.send(f"⚠️ 根據你輸入的生日 {birthdate_str}，你未滿 {MINIMUM_AGE} 歲。")
            else:
                await verification_channel.send(f"❌ 你輸入的 `{date_message.content}` 格式不正確或日期無效。")
        except asyncio.TimeoutError:
            await verification_channel.send("⌛ 手動輸入超時。")


    # 賦予身份組和刪除頻道
    if age_verified:
        if verified_role:
            try:
                if guild.me.top_role > verified_role: # 檢查 Bot 的身份組權限是否足夠
                    await member.add_roles(verified_role)
                    await verification_channel.send(f"🎉 恭喜！你已獲得 `{VERIFIED_ROLE_NAME}` 身份組！")
                else:
                    await verification_channel.send(f"⚠️ Bot 的身份組權限不足以賦予 `{VERIFIED_ROLE_NAME}`，請聯絡管理員調整 Bot 的身份組順序。")
            except discord.Forbidden:
                await verification_channel.send(f"❌ Bot 無法賦予身份組，請聯絡管理員檢查權限。")
            except Exception as e:
                await verification_channel.send(f"賦予身份組時發生錯誤: {e}")
        else:
            await verification_channel.send(f"⚠️ 找不到名為 `{VERIFIED_ROLE_NAME}` 的身份組，請管理員先建立此身份組。")
    else:
        await verification_channel.send("❌ 驗證未通過。此頻道將在一段時間後自動刪除。如有疑問請聯絡管理員。")

    # 關於 "logs channels會在1小時內把用戶的資料刪除"：
    # 目前的設計是驗證頻道本身在完成後不久就會刪除，這樣使用者的圖片和對話記錄就不會長時間保留。
    # 如果你需要一個獨立的日誌頻道來記錄誰驗證了，然後再定時刪除日誌，那會需要更複雜的設計。
    # 目前的作法是直接刪除整個驗證頻道，相對簡單且保護隱私。
    delete_delay = 15 # 預設15秒後刪除頻道
    if age_verified:
        await verification_channel.send(f"此頻道將在 {delete_delay} 秒後自動刪除...")
    else: # 未通過驗證，給予更長一點的時間查看訊息
        delete_delay = 60 
        await verification_channel.send(f"此頻道將在 {delete_delay} 秒後自動刪除...")

    await asyncio.sleep(delete_delay)
    try:
        await verification_channel.delete(reason="年齡驗證完成")
    except discord.Forbidden:
        print(f"無法刪除頻道 {verification_channel.name}：沒有權限。")
    except discord.NotFound:
        print(f"無法刪除頻道 {verification_channel.name}：頻道已被刪除。")
    except Exception as e:
        print(f"刪除頻道時發生錯誤: {e}")


# ── Slash 指令 /verify ─────────────────────────────
# 建議在 Discord 伺服器設定一個專用的 #年齡驗證 文字頻道，讓使用者只能在這裡用指令。
@bot.tree.command(name="verify", description="開始年齡驗證流程 (請在指定的 #年齡驗證 頻道使用)")
async def verify_slash_command(interaction: discord.Interaction):
    # 你可以限制此指令只能在特定頻道使用
    # if interaction.channel.name != "年齡驗證": # 假設你的指定頻道名稱是 "年齡驗證"
    #     await interaction.response.send_message("請在 #年齡驗證 頻道使用此指令！", ephemeral=True)
    #     return

    # 直接開始流程，流程內部會發送ephemeral訊息並建立頻道
    asyncio.create_task(start_verify_flow(interaction.guild, interaction.user, interaction))

# ── Persistent 按鈕 (可選，如果你想用按鈕觸發) ──────────────────
class VerificationButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # timeout=None 使按鈕永久有效

    @discord.ui.button(label="🔞 點我開始年齡驗證", style=discord.ButtonStyle.primary, custom_id="persistent_verify_button")
    async def verify_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 按鈕的回應需要用 interaction.response.send_message (如果尚未回應) 或 interaction.followup.send
        # 我們讓 start_verify_flow 內部處理回應
        await interaction.response.send_message("正在為你準備驗證頻道...", ephemeral=True) # 先給使用者一個快速回應
        asyncio.create_task(start_verify_flow(interaction.guild, interaction.user, interaction.followup)) # 使用 followup 因為已經 response過了

# ── Bot 設定指令 (例如：發送帶有驗證按鈕的訊息) ─────────────────
@bot.command(name="setupverifybutton")
@commands.has_permissions(administrator=True) # 只有管理員能用這個指令
async def setup_verify_button_command(ctx: commands.Context):
    """
    管理員指令：發送一個帶有永久驗證按鈕的訊息到目前頻道。
    """
    view = VerificationButtonView()
    await ctx.send("歡迎來到本伺服器！請點擊下方按鈕進行年齡驗證以解鎖更多頻道：", view=view)
    await ctx.message.delete() # 刪除指令訊息 (可選)

@setup_verify_button_command.error
async def setup_verify_button_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("你沒有權限使用此指令。", delete_after=10)
        await ctx.message.delete(delay=10)

# ── Bot 就緒事件 ───────────────────────────────────
@bot.event
async def on_ready():
    print(f"Bot 已登入為: {bot.user.name} (ID: {bot.user.id})")
    print(f"正在監聽伺服器 ID: {GUILD_ID}")
    print("------")

    # 同步特定伺服器的斜線指令 (如果你只在一個伺服器用)
    # 如果 GUILD_ID 沒設定或設為 0，則會註冊為全域指令 (可能需要1小時生效)
    # 為了快速測試，建議設定 GUILD_ID 並在此同步
    if GUILD_ID and GUILD_ID != 1234567890123456789: # 確保 GUILD_ID 被修改過
        guild_object = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_object)
        try:
            await bot.tree.sync(guild=guild_object)
            print(f"已成功同步斜線指令到伺服器 {GUILD_ID}")
        except discord.Forbidden:
            print(f"錯誤：無法同步斜線指令到伺服器 {GUILD_ID}。請確保 Bot 有 'application.commands' 權限並且已被邀請到該伺服器。")
        except Exception as e:
            print(f"同步斜線指令時發生錯誤: {e}")
    else:
        try:
            await bot.tree.sync() # 同步全域指令 (可能較慢)
            print("已同步全域斜線指令 (可能需要一些時間在所有伺服器生效)")
        except Exception as e:
            print(f"同步全域斜線指令時發生錯誤: {e}")

    # 添加永久視圖 (如果 Bot 重啟，按鈕仍然可以運作)
    bot.add_view(VerificationButtonView())
    print("永久驗證按鈕視圖已添加。")


# ── 主程式入口 ───────────────────────────────────────
if __name__ == "__main__":
    # 從 Replit Secrets 讀取 Bot Token
    bot_token = os.getenv("BOT_TOKEN")

    if not bot_token:
        print("🚨 錯誤：找不到 BOT_TOKEN！請在 Replit 的 'Secrets' 中設定 BOT_TOKEN。")
        print("設定方法：左邊工具欄點擊 'Secrets' (鎖頭圖示)，")
        print("然後新增一個 Secret，Key 輸入 BOT_TOKEN，Value 輸入你的 Discord Bot Token。")
    else:
        try:
            bot.run(bot_token)
        except discord.LoginFailure:
            print("🚨 錯誤：BOT_TOKEN 不正確或無效，登入失敗。請檢查你的 Token。")
        except Exception as e:
            print(f"🚨 啟動 Bot 時發生未預期錯誤: {e}")