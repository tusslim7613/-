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
# ⚙️ ส่วนตั้งค่า
# ============================================================
SERVER_ID = 806041410607775745  # 👈 ID เซิร์ฟเวอร์ของคุณ

# 1. ใส่ ID ห้อง "กระดานเควส" ที่แยกตามเลเวล/Tier
QUEST_BOARD_TIER_1 = 1569061816619896862
QUEST_BOARD_TIER_2 = 1569234984320114850
QUEST_BOARD_TIER_3 = 1569235058022424637
QUEST_BOARD_TIER_4 = 1569235104662945884

# 2. ใส่ ID ห้อง "ห้อง Join เควส" (ที่เดียวรวมทุกระดับเลเวล)
QUEST_JOIN_FORUM_ID = 1569061876711694447
DM_ROLE_NAME = "DM"  # ชื่อ Role ของ DM


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

        # 1. แก้ไขข้อความในโพสต์ (หาบรรทัดเก่าแล้วแทนที่ด้วยเวลาใหม่)
        old_content = message.content
        lines = old_content.split('\n')
        new_lines = []
        for line in lines:
            if line.startswith("🗓️ **วันเวลาที่สะดวก:**"):
                new_lines.append(f"🗓️ **วันเวลาที่สะดวก:** {self.new_schedule.value}")
            else:
                new_lines.append(line)
        new_content = '\n'.join(new_lines)

        # 2. แก้ไขชื่อ Thread (หัวกระทู้)
        old_thread_name = thread.name
        # ตัดเวลาเก่าที่อยู่ในวงเล็บ [...] ออก แล้วเอาเวลาใหม่ใส่เข้าไป
        if "] " in old_thread_name:
            suffix = old_thread_name.split("] ", 1)[1]
        else:
            suffix = old_thread_name
        
        new_thread_name = f"[{self.new_schedule.value}] {suffix}"[:100]

        try:
            # อัปเดตทั้งชื่อกระทู้ และ ข้อความ
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
        # ✅ เช็คสิทธิ์: ให้แก้ได้เฉพาะ "ผู้จองเควส" หรือ "DM" เท่านั้น
        is_owner = str(interaction.user.id) in interaction.message.content
        is_dm = discord.utils.get(interaction.user.roles, name=DM_ROLE_NAME) is not None

        if not (is_owner or is_dm):
            await interaction.response.send_message("❌ คุณไม่ใช่ผู้จองเควสนี้ หรือไม่ใช่ DM ไม่สามารถแก้ไขเวลาได้ครับ", ephemeral=True)
            return

        # เปิดหน้าต่างให้กรอกเวลาใหม่
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

        # นำเวลาไปใส่ไว้หน้าสุดของชื่อกระทู้
        thread_name = f"[{self.schedule.value}] {self.quest_title} - {interaction.user.display_name}"[:100]

        kwargs = {
            "name": thread_name,
            "content": content,
            "allowed_mentions": discord.AllowedMentions(users=[interaction.user]),
            "view": EditTimeView()  # ✅ เพิ่มปุ่มแก้ไขเวลาเข้าไปในโพสต์นี้
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
        # ลงทะเบียนปุ่มให้ทำงานได้ตลอดแม้บอทรีสตาร์ท
        self.add_view(BookingView())
        self.add_view(EditTimeView())  # ✅ ลงทะเบียนปุ่มแก้ไขเวลา

        my_guild = discord.Object(id=SERVER_ID)
        self.tree.copy_global_to(guild=my_guild)
        # ไม่ต้อง sync อัตโนมัติ ป้องกันโดนแบน API ให้พิมพ์ !sync เอาเอง

    async def on_ready(self):
        print(f"✅ ล็อกอินแล้วในชื่อ {self.user} (ID: {self.user.id})")
        print(f"✅ ซิงค์ข้อมูลคำสั่งสำเร็จ! ลองไปพิมพ์ /create_quest ในดิสคอร์ดดูได้เลย")


bot = QuestBot()


# ============================================================
# 🔧 คำสั่งพิเศษสำหรับ Admin (ใช้ซิงค์ Slash Command)
# ============================================================
@bot.command()
@commands.is_owner()
async def sync(ctx):
    try:
        my_guild = discord.Object(id=SERVER_ID)
        synced = await bot.tree.sync(guild=my_guild)
        await ctx.send(f"🔄 ซิงค์ Slash Commands สำเร็จจำนวน {len(synced)} คำสั่ง")
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
@app_commands.checks.has_role(DM_ROLE_NAME)
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
        msg = f"⛔ คำสั่งนี้ใช้ได้เฉพาะผู้ที่มี Role **{DM_ROLE_NAME}** เท่านั้น"
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
        server_on()  # รันเซิร์ฟเวอร์เพื่อให้เว็บ uptime ตรวจจับได้
        bot.run(token)
    else:
        print("❌ ไม่พบ DISCORD_TOKEN ในไฟล์ .env")
