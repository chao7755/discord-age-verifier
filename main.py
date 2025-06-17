from flask import Flask
from threading import Thread
import discord
from discord.ext import commands
from discord.ui import Button, View

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# 設置 bot 的權限和指令前綴
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 定義身份組（替換為你的 Role ID）
ROLE_IDS = {
    "設計組🖌️": 1384517681813786645,  # 替換為實際 Role ID
    "道具定制組📕": 1384517791905742918,
    "大屏組📸": 1384517910126399619,
    "投放組📥": 1384518000891396207,
    "財務組💰": 1384518094432632983,
    "有應援活動經歷": 1384518176028626997
}

# 創建按鈕的 View 類
class RoleButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def add_remove_role(self, interaction: discord.Interaction, role_id: int, role_name: str):
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(f"身份組 {role_name} 不存在！", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"已移除身份組：{role_name}", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"已領取身份組：{role_name}", ephemeral=True)

    @discord.ui.button(label="設計組🖌️", style=discord.ButtonStyle.green, row=1)
    async def design_button(self, interaction: discord.Interaction, button: Button):
        await self.add_remove_role(interaction, ROLE_IDS["設計組🖌️"], "設計組🖌️")

    @discord.ui.button(label="道具定制組📕", style=discord.ButtonStyle.green, row=1)
    async def props_button(self, interaction: discord.Interaction, button: Button):
        await self.add_remove_role(interaction, ROLE_IDS["道具定制組📕"], "道具定制組📕")

    @discord.ui.button(label="大屏組📸", style=discord.ButtonStyle.green, row=1)
    async def screen_button(self, interaction: discord.Interaction, button: Button):
        await self.add_remove_role(interaction, ROLE_IDS["大屏組📸"], "大屏組📸")

    @discord.ui.button(label="投放組📥", style=discord.ButtonStyle.green, row=2)
    async def promotion_button(self, interaction: discord.Interaction, button: Button):
        await self.add_remove_role(interaction, ROLE_IDS["投放組📥"], "投放組📥")

    @discord.ui.button(label="財務組💰", style=discord.ButtonStyle.green, row=2)
    async def finance_button(self, interaction: discord.Interaction, button: Button):
        await self.add_remove_role(interaction, ROLE_IDS["財務組💰"], "財務組💰")

    @discord.ui.button(label="有應援活動經歷", style=discord.ButtonStyle.blurple, row=3)
    async def event_button(self, interaction: discord.Interaction, button: Button):
        await self.add_remove_role(interaction, ROLE_IDS["有應援活動經歷"], "有應援活動經歷")

# 指令：發送帶按鈕的消息
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_roles(ctx):
    embed = discord.Embed(
        title="領取身份組",
        description="點擊下方按鈕領取或移除對應的身份組：\n"
                    "🖌️ 設計組\n📕 道具定制組\n📸 大屏組\n📥 投放組\n💰 財務組\n"
                    "🔥 有應援活動經歷",
        color=discord.Color.blue()
    )
    view = RoleButtonView()
    await ctx.send(embed=embed, view=view)

@bot.event
async def on_ready():
    print(f"Bot 已上線，名稱：{bot.user}")

# 錯誤處理
@setup_roles.error
async def setup_roles_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("你需要管理員權限才能使用此指令！")
    else:
        await ctx.send("發生錯誤，請檢查 bot 權限或聯繫管理員！")

# 啟動 Flask 和 bot
def main():
    Thread(target=run_flask).start()
    bot.run("YOUR_BOT_TOKEN")  # 替換為你的 bot token

if __name__ == "__main__":
    main()
