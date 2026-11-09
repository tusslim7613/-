import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import io
import aiohttp
from MyServer import server_on  # ✅ นำเข้าฟังก์ชันเปิดเซิร์ฟเวอร์

load_dotenv()

# ============================================================
# ⚙️ ส่วนตั้งค่า (⚠️ ถ้าย้ายเซิร์ฟเวอร์ ต้องแก้ ID พวกนี้ใหม่ทั้งหมด!)
# ============================================================
# 1. ใส่ ID ห้อง "กระดานเควส" ของเซิร์ฟเวอร์ใหม่
QUEST_BOARD_TIER_1 = 1442689482401779864
QUEST_BOARD_TIER_2 = 1442705819455197335
QUEST_BOARD_TIER_3 = 1442711022434521220
QUEST_BOARD_TIER_4 = 1442714869966311535

# 2. ใส่ ID ห้อง "ห้อง Join เควส" ของเซิร์ฟเวอร์ใหม่
QUEST_JOIN_FORUM_ID = 1387274730330914907

# 3. ใส่ Role ID ของคนคุมเควสในเซิร์ฟเวอร์ใหม่
DM_ROLE_ID = 123456789012345678  # 👈 เปลี่ยนเป็น Role ID ของเซิร์ฟใหม่


# ============================================================
# 🛠️ Modal & View สำหรับการ "แก้ไขเวลา" ในห้อง Join เควส
# ============================================================
class EditTimeModal(discord.ui.Modal, title="เลื่อน / แก้ไขเวลา"):
    new_schedule = discord.ui.TextInput(
        label="ระบุวันและเวลาใหม่",
        placeholder="เช่น 02/12/26 21.00",
        style=discord.TextStyle.short,
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        thread = interaction.channel

        old_content = message.content
        lines = old_content.split('\n')
        new_lines = []
        for line in lines:
            if line.startswith("🗓️ **วันเวลาที่สะดวก:**"):
                new_lines.append(f"🗓️ **วันเวลาที่สะดวก:** {self.new_schedule.value}")
            else:
                new_lines.append(line)
        new_content = '\n'.join(new_lines)

        old_thread_name = thread.name
        if "] " in old_thread_name:
            suffix = old_thread_name.split("] ", 1)[1]
        else:
            suffix = old_thread_name
        
        new_thread_name = f"[{self.new_schedule.value}] {suffix}"[:100]

        try:
            await thread.edit(name=new_thread_name)
            await message.edit(content=new_content)
            await interaction.followup.send("✅ แก้ไขเวลานัดหมายเรียบร้อยแล้ว!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการอัปเดต: {e}", ephemeral=True)


class EditTimeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(
        label="⏳ เลื่อน/แก้ไขเวลา",
        style=discord.ButtonStyle.primary,
        custom_id="quest:edit_time"
    )
    async def edit_time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_owner = str(interaction.user.id) in interaction.message.content
        is_dm = any(role.id == DM_ROLE_ID for role in interaction.user.roles)

        if not (is_owner or is_dm):
            await interaction.response.send_message("❌ คุณไม่ใช่ผู้จองเควสนี้ หรือไม่ใช่ DM ไม่สามารถแก้ไขเวลาได้ครับ", ephemeral=True)
            return

        await interaction.response.send_modal(EditTimeModal())


# ============================================================
# 🧩 Modal: หน้าต่างกรอกวันเวลาของผู้เล่นตอนจองครั้งแรก
# ============================================================
class BookingModal(discord.ui.Modal, title="จองเควส"):
    schedule = discord.ui.TextInput(
        label="ระบุวันและเวลาที่คุณสะดวกเล่น",
        placeholder="เช่น [01/12/26 20.00] ",
        style=discord.TextStyle.short,
        required=True,
        max_length=50,
    )

    def __init__(self, quest_title: str, quest_description: str, recommended_level: str, image_url: str = None):
        super().__init__()
        self.quest_title = quest_title
        self.quest_description = quest_description
        self.recommended_level = recommended_level
        self.image_url = image_url

    async def on_submit(self, interaction: discord.Interaction):
        join_forum = interaction.client.get_channel(QUEST_JOIN_FORUM_ID)

        if not isinstance(join_forum, discord.ForumChannel):
            await interaction.response.send_message(
                "❌ ไม่พบห้อง Join เควส กรุณาแจ้งผู้ดูแลเซิร์ฟเวอร์ให้ตรวจสอบ ID ห้อง",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        content = (
            f"📥 **บันทึกการจองเควส:** {self.quest_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🙋 **ผู้เล่นที่จอง:** {interaction.user.mention}\n"
            f"🗓️ **วันเวลาที่สะดวก:** {self.schedule.value}\n"
            f"⚔️ **เลเวลที่แนะนำ:** {self.recommended_level}\n\n"
            f"📜 **รายละเอียดเควส:**\n{self.quest_description}"
        )

        file_to_send = None
        if self.image_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.image_url) as resp:
                        if resp.status == 200:
                            data = io.BytesIO(await resp.read())
                            file_to_send = discord.File(data, filename="quest_image.png")
                        else:
                            content += f"\n\n{self.image_url}"
            except Exception:
                content += f"\n\n{self.image_url}"

        thread_name = f"[{self.schedule.value}] {self.quest_title} - {interaction.user.display_name}"[:100]

        kwargs = {
            "name": thread_name,
            "content": content,
            "allowed_mentions": discord.AllowedMentions(users=[interaction.user]),
            "view": EditTimeView() 
        }

        if file_to_send:
            kwargs["file"] = file_to_send

        await join_forum.create_thread(**kwargs)
        await interaction.followup.send("✅ ส่งคำขอจองเควสสำเร็จแล้ว! รอ DM ติดต่อกลับได้เลย", ephemeral=True)


# ============================================================
# 🟩 View: ปุ่ม "จองเควสนี้" ในกระดานเควส
# ============================================================
class BookingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="จองเควสนี้",
        style=discord.ButtonStyle.success,
        custom_id="quest:book",
    )
    async def book_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        message = interaction.message
        quest_title = "ไม่ทราบชื่อเควส"
        quest_description = "-"
        recommended_level = "ไม่ระบุ"
        image_url = None

        if message.content:
            lines = message.content.split('\n')
            for line in lines:
                if line.startswith("📌 **เควส:**"):
                    quest_title = line.replace("📌 **เควส:**", "").strip()
                elif line.startswith("⚔️ **เลเวลที่แนะนำ:**"):
                    recommended_level = line.replace("⚔️ **เลเวลที่แนะนำ:**", "").strip()

            if "📜 **รายละเอียด:**" in message.content:
                quest_description = message.content.split("📜 **รายละเอียด:**")[1].strip()

                desc_lines = quest_description.split('\n')
                if desc_lines and desc_lines[-1].startswith(("http://", "https://")):
                    desc_lines.pop()
                    quest_description = "\n".join(desc_lines).strip()

        if message.attachments:
            image_url = message.attachments[0].url
        else:
            last_line = message.content.split('\n')[-1].strip()
            if last_line.startswith(("http://", "https://")):
                image_url = last_line

        await interaction.response.send_modal(
            BookingModal(quest_title, quest_description, recommended_level, image_url)
        )


# ============================================================
# 🤖 ตัวบอทหลัก
# ============================================================
class QuestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(BookingView())
        self.add_view(EditTimeView())

    async def on_ready(self):
        print(f"✅ ล็อกอินแล้วในชื่อ {self.user} (ID: {self.user.id})")
        print(f"✅ พร้อมใช้งาน! หากย้ายเซิร์ฟเวอร์ ให้พิมพ์ !sync ในดิสคอร์ดเซิร์ฟเวอร์นั้นๆ")


bot = QuestBot()


# ============================================================
# 🔧 คำสั่งพิเศษสำหรับ Admin (ใช้ซิงค์ Slash Command ให้ตรงกับเซิร์ฟนั้นๆ)
# ============================================================
@bot.command()
@commands.is_owner()
async def sync(ctx):
    try:
        # ✅ เปลี่ยนมาดึง ID เซิร์ฟเวอร์จากห้องที่พิมพ์คำสั่งอัตโนมัติ
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"🔄 ซิงค์ Slash Commands ลงเซิร์ฟเวอร์ '{ctx.guild.name}' สำเร็จจำนวน {len(synced)} คำสั่ง")
    except Exception as e:
        await ctx.send(f"❌ เกิดข้อผิดพลาดในการซิงค์: {e}")


# ============================================================
# 🧙 Slash Command: /create_quest
# ============================================================
@bot.tree.command(name="create_quest", description="สร้างเควสใหม่บนกระดานเควสพร้อมรูปภาพ (เฉพาะ DM)")
@app_commands.describe(
    quest_board="เลือกห้องกระดานเควสตาม Tier",
    title="ชื่อเควส",
    dm_name="ชื่อ DM ที่คุมเควสนี้ (พิมพ์ชื่อเองได้เลย)",
    description="คำอธิบายเควส",
    max_players="จำนวนผู้เล่นที่รับ",
    recommended_level="ระดับเลเวลที่แนะนำ (เช่น เลเวล 3, หรือ 1-4)",
    image_file="อัปโหลดรูปภาพโดยตรง (ลากรูปใส่ช่องนี้)",
    image_url="หรือ วางลิงก์รูปภาพเว็บนอก (เช่น https://...)"
)
@app_commands.choices(quest_board=[
    app_commands.Choice(name="กระดาน Tier 1 (Lv 1-4)", value=str(QUEST_BOARD_TIER_1)),
    app_commands.Choice(name="กระดาน Tier 2 (Lv 5-10)", value=str(QUEST_BOARD_TIER_2)),
    app_commands.Choice(name="กระดาน Tier 3 (Lv 11-16)", value=str(QUEST_BOARD_TIER_3)),
    app_commands.Choice(name="กระดาน Tier 4 (Lv 17-20)", value=str(QUEST_BOARD_TIER_4)),
])
@app_commands.guild_only()
@app_commands.checks.has_role(DM_ROLE_ID)
async def create_quest(
        interaction: discord.Interaction,
        quest_board: app_commands.Choice[str],
        title: app_commands.Range[str, 1, 90],
        dm_name: app_commands.Range[str, 1, 100],
        description: app_commands.Range[str, 1, 1500],
        max_players: app_commands.Range[int, 1, 20],
        recommended_level: app_commands.Range[str, 1, 20],
        image_file: discord.Attachment = None,
        image_url: str = None
):
    board_forum = interaction.client.get_channel(int(quest_board.value))

    if not isinstance(board_forum, discord.ForumChannel):
        await interaction.response.send_message(
            f"❌ ไม่พบห้อง {quest_board.name} กรุณาตรวจสอบ ID ห้องในโค้ดตั้งค่า",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    content = (
        f"📌 **เควส:** {title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **รับผู้เล่น:** {max_players} คน\n"
        f"⚔️ **เลเวลที่แนะนำ:** {recommended_level}\n"
        f"👑 **DM:** {dm_name}\n\n"
        f"📜 **รายละเอียด:**\n{description}"
    )

    file_to_send = None

    if image_file:
        if any(image_file.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
            file_to_send = await image_file.to_file()
    elif image_url:
        if image_url.startswith(("http://", "https://")):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status == 200:
                            data = io.BytesIO(await resp.read())
                            file_to_send = discord.File(data, filename="quest_image.png")
                        else:
                            content += f"\n\n{image_url}"
            except Exception:
                content += f"\n\n{image_url}"

    kwargs = {
        "name": title,
        "content": content,
        "view": BookingView()
    }

    if file_to_send:
        kwargs["file"] = file_to_send

    await board_forum.create_thread(**kwargs)
    await interaction.followup.send(f"✅ สร้างเควสบน {quest_board.name} เรียบร้อยแล้ว", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        msg = f"⛔ คำสั่งนี้ใช้ได้เฉพาะผู้ที่มี Role <@&{DM_ROLE_ID}> เท่านั้น"
    else:
        msg = "❌ เกิดข้อผิดพลาดบางอย่าง กรุณาลองใหม่อีกครั้ง"
        print(f"[Error] {error!r}")

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token:
        server_on()  
        bot.run(token)
    else:
        print("❌ ไม่พบ DISCORD_TOKEN ในไฟล์ .env")
