import os
import json
import re
import io
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# พยายามนำเข้า MyServer (ถ้าไม่มีไฟล์นี้ก็ทำงานได้ตามปกติ)
try:
    from MyServer import server_on
    HAS_MYSERVER = True
except ImportError:
    HAS_MYSERVER = False

load_dotenv()

# ============================================================
# 🗄️ ระบบจัดการการตั้งค่าต่อเซิร์ฟเวอร์ (Multi-Guild Config)
#    เก็บในไฟล์ guild_configs.json — ไม่ต้องแก้โค้ดอีกต่อไป!
# ============================================================
CONFIG_FILE = "guild_configs.json"

def load_configs() -> dict:
    """โหลดการตั้งค่าทั้งหมดจากไฟล์"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_configs(configs: dict):
    """บันทึกการตั้งค่าทั้งหมดลงไฟล์"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2, ensure_ascii=False)

def get_guild_config(guild_id: int) -> dict:
    """ดึงการตั้งค่าของเซิร์ฟเวอร์หนึ่งๆ"""
    return load_configs().get(str(guild_id), {})

def update_guild_config(guild_id: int, **kwargs):
    """อัปเดตการตั้งค่าของเซิร์ฟเวอร์หนึ่งๆ"""
    configs = load_configs()
    gid = str(guild_id)
    if gid not in configs:
        configs[gid] = {}
    configs[gid].update(kwargs)
    save_configs(configs)


# ============================================================
# 🛡️ Custom Check: ตรวจสอบว่าผู้ใช้มี DM Role ของเซิร์ฟเวอร์นั้นๆ หรือไม่
# ============================================================
def is_dm_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        config = get_guild_config(interaction.guild_id)
        dm_role_id = config.get("dm_role_id")
        if not dm_role_id:
            raise app_commands.CheckFailure(
                "เซิร์ฟเวอร์นี้ยังไม่ได้ตั้งค่า DM Role — ให้ผู้ดูแลใช้คำสั่ง /setup ก่อน"
            )
        return any(role.id == int(dm_role_id) for role in interaction.user.roles)
    return app_commands.check(predicate)


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
        # 🔑 ดึง DM Role ID จาก config ของเซิร์ฟเวอร์นี้ (ไม่ใช่ค่าคงที่อีกต่อไป)
        config = get_guild_config(interaction.guild_id)
        dm_role_id = config.get("dm_role_id")
        is_dm = bool(dm_role_id) and any(role.id == int(dm_role_id) for role in interaction.user.roles)

        # 🔍 ตรวจสอบเจ้าของเควสอย่างแม่นยำด้วย regex (หา <@user_id> จากข้อความ)
        is_owner = False
        content = interaction.message.content or ""
        for line in content.split('\n'):
            if "**ผู้เล่นที่จอง:**" in line:
                match = re.search(r'<@(\d+)>', line)
                if match and match.group(1) == str(interaction.user.id):
                    is_owner = True
                break

        if not (is_owner or is_dm):
            await interaction.response.send_message(
                "❌ คุณไม่ใช่ผู้จองเควสนี้ หรือไม่ใช่ DM ไม่สามารถแก้ไขเวลาได้ครับ",
                ephemeral=True
            )
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
        # 🔑 ดึงห้อง Join เควสจาก config ของเซิร์ฟเวอร์นี้
        config = get_guild_config(interaction.guild_id)
        join_forum_id = config.get("quest_join_forum_id")

        if not join_forum_id:
            await interaction.response.send_message(
                "❌ เซิร์ฟเวอร์นี้ยังไม่ได้ตั้งค่าห้อง Join เควส — ให้ผู้ดูแลใช้คำสั่ง /setup ก่อน",
                ephemeral=True,
            )
            return

        join_forum = interaction.client.get_channel(int(join_forum_id))

        if not isinstance(join_forum, discord.ForumChannel):
            await interaction.response.send_message(
                "❌ ห้อง Join เควสที่ตั้งค่าไว้ไม่ถูกต้อง (ต้องเป็น Forum Channel) — ให้ผู้ดูแลใช้ /setup ตั้งค่าใหม่",
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
        print(f"✅ รองรับหลายเซิร์ฟเวอร์! ในแต่ละเซิร์ฟเวอร์ ให้ Admin ใช้ /setup ตั้งค่า แล้วพิมพ์ !sync")
        print(f"📋 จำนวนเซิร์ฟเวอร์ที่บอทเข้าร่วม: {len(self.guilds)}")


bot = QuestBot()


# ============================================================
# 🔧 คำสั่งพิเศษสำหรับ Owner (ซิงค์ Slash Command ลงแต่ละเซิร์ฟเวอร์)
# ============================================================
@bot.command()
@commands.is_owner()
async def sync(ctx):
    try:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"🔄 ซิงค์ Slash Commands ลงเซิร์ฟเวอร์ '{ctx.guild.name}' สำเร็จจำนวน {len(synced)} คำสั่ง")
    except Exception as e:
        await ctx.send(f"❌ เกิดข้อผิดพลาดในการซิงค์: {e}")


# ============================================================
# ⚙️ Slash Command: /setup — ตั้งค่าบอทสำหรับเซิร์ฟเวอร์นี้ (ครั้งเดียวต่อเซิร์ฟเวอร์)
# ============================================================
@bot.tree.command(name="setup", description="ตั้งค่าบอท Quest สำหรับเซิร์ฟเวอร์นี้ (เฉพาะผู้ดูแล)")
@app_commands.describe(
    quest_join_forum="เลือกห้อง Forum ที่ใช้เป็น 'ห้อง Join เควส'",
    dm_role="เลือก Role ที่เป็นคนคุมเควส (DM)",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(
    interaction: discord.Interaction,
    quest_join_forum: discord.ForumChannel,
    dm_role: discord.Role,
):
    update_guild_config(
        interaction.guild_id,
        quest_join_forum_id=quest_join_forum.id,
        dm_role_id=dm_role.id,
    )
    await interaction.response.send_message(
        f"✅ ตั้งค่าเซิร์ฟเวอร์เรียบร้อยแล้ว!\n\n"
        f"📌 **ห้อง Join เควส:** {quest_join_forum.mention}\n"
        f"👑 **DM Role:** {dm_role.mention}\n\n"
        f"ตอนนี้สามารถใช้ /create_quest และระบบจองเควสได้แล้วครับ",
        ephemeral=True,
    )


# ============================================================
# 📋 Slash Command: /show_config — แสดงการตั้งค่าปัจจุบัน
# ============================================================
@bot.tree.command(name="show_config", description="แสดงการตั้งค่าบอทของเซิร์ฟเวอร์นี้ (เฉพาะผู้ดูแล)")
@app_commands.checks.has_permissions(manage_guild=True)
async def show_config(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild_id)
    if not config:
        await interaction.response.send_message(
            "⚠️ เซิร์ฟเวอร์นี้ยังไม่ได้ตั้งค่า — ให้ใช้คำสั่ง /setup ก่อน",
            ephemeral=True,
        )
        return

    join_forum_id = config.get("quest_join_forum_id")
    dm_role_id = config.get("dm_role_id")

    join_forum = interaction.client.get_channel(int(join_forum_id)) if join_forum_id else None
    dm_role = interaction.guild.get_role(int(dm_role_id)) if dm_role_id else None

    lines = ["📋 **การตั้งค่าปัจจุบันของเซิร์ฟเวอร์นี้:**\n"]
    lines.append(f"📌 ห้อง Join เควส: {join_forum.mention if join_forum else '❌ ไม่พบห้อง (ตั้งค่าใหม่ด้วย /setup)'}")
    lines.append(f"👑 DM Role: {dm_role.mention if dm_role else '❌ ไม่พบ Role (ตั้งค่าใหม่ด้วย /setup)'}")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


# ============================================================
# 🧙 Slash Command: /create_quest
#    🔑 quest_board เป็นการเลือกห้อง Forum โดยตรงจาก dropdown — ใช้ได้ทุกเซิร์ฟเวอร์ทันที!
# ============================================================
@bot.tree.command(name="create_quest", description="สร้างเควสใหม่บนกระดานเควสพร้อมรูปภาพ (เฉพาะ DM)")
@app_commands.describe(
    quest_board="เลือกห้อง Forum กระดานเควสที่ต้องการลง (เช่น Tier 1, Tier 2 ฯลฯ)",
    title="ชื่อเควส",
    dm_name="ชื่อ DM ที่คุมเควสนี้ (พิมพ์ชื่อเองได้เลย)",
    description="คำอธิบายเควส",
    max_players="จำนวนผู้เล่นที่รับ",
    recommended_level="ระดับเลเวลที่แนะนำ (เช่น เลเวล 3, หรือ 1-4)",
    image_file="อัปโหลดรูปภาพโดยตรง (ลากรูปใส่ช่องนี้)",
    image_url="หรือ วางลิงก์รูปภาพเว็บนอก (เช่น https://...)"
)
@app_commands.guild_only()
@is_dm_role()
async def create_quest(
        interaction: discord.Interaction,
        quest_board: discord.ForumChannel,
        title: app_commands.Range[str, 1, 90],
        dm_name: app_commands.Range[str, 1, 100],
        description: app_commands.Range[str, 1, 1500],
        max_players: app_commands.Range[int, 1, 20],
        recommended_level: app_commands.Range[str, 1, 20],
        image_file: discord.Attachment = None,
        image_url: str = None
):
    # quest_board เป็น ForumChannel object โดยตรง — ไม่ต้อง get_channel ด้วย ID อีกต่อไป
    board_forum = quest_board

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
    await interaction.followup.send(f"✅ สร้างเควสในห้อง {board_forum.mention} เรียบร้อยแล้ว", ephemeral=True)


# ============================================================
# 🚨 Error Handler สำหรับ Slash Command
# ============================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        msg = "⛔ คุณไม่มีสิทธิ์ใช้คำสั่งนี้"
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "⛔ คำสั่งนี้ใช้ได้เฉพาะผู้ดูแลเซิร์ฟเวอร์ (มีสิทธิ์ Manage Server) เท่านั้น"
    elif isinstance(error, app_commands.CheckFailure):
        msg = f"⛔ {error}"
    else:
        msg = "❌ เกิดข้อผิดพลาดบางอย่าง กรุณาลองใหม่อีกครั้ง"
        print(f"[Error] {error!r}")

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ============================================================
# 🚀 เริ่มรันบอท
# ============================================================
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token:
        if HAS_MYSERVER:
            server_on()
        bot.run(token)
    else:
        print("❌ ไม่พบ DISCORD_TOKEN ในไฟล์ .env")
