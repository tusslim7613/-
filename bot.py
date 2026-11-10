import os
import json
import re
import io
import traceback
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
# 🚨 Helper: จัดการ error ที่เกิดจากปุ่ม (View) — แสดงรายละเอียดให้ชัดเจน
# ============================================================
async def _handle_view_error(interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
    print(f"[View Error] item={getattr(item, 'custom_id', '?')}: {type(error).__name__}: {error!r}")
    traceback.print_exception(type(error), error, error.__traceback__)

    if isinstance(error, discord.Forbidden):
        msg = (
            "⛔ บอทไม่มีสิทธิ์ดำเนินการนี้\n"
            "สาเหตุที่พบบ่อย: บอทไม่มีสิทธิ์จัดการกระทู้/แก้ไขข้อความในห้องนี้ "
            "ให้ตรวจสอบสิทธิ์ของบอทในห้อง Forum นั้นๆ (คลิกขวาที่ห้อง → Edit Channel → Permissions)"
        )
    elif isinstance(error, discord.HTTPException):
        msg = f"❌ Discord API Error: {error.status} — {error.text}"
    else:
        error_type = type(error).__name__
        error_msg = str(error)[:200]
        msg = (
            f"❌ เกิดข้อผิดพลาด: `{error_type}`\n"
            f"รายละเอียด: {error_msg}\n\n"
            f"กรุณาส่งข้อความนี้ (พร้อม log ใน Terminal) ให้ผู้พัฒนาเพื่อตรวจสอบต่อ"
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ============================================================
# 🏷️ Helper: เลือก Tag อัตโนมัติสำหรับ Forum Channel ที่บังคับใส่ Tag
# ============================================================
def _pick_forum_tags(forum_channel: discord.ForumChannel) -> list:
    """ถ้าห้อง Forum ต้องการ tag ตอนสร้างกระทู้ ให้เลือก tag ที่เหมาะสมอัตโนมัติ"""
    tags = forum_channel.available_tags
    if not tags:
        return []
    # เลือก tag ที่ไม่ต้องมีสิทธิ์พิเศษก่อน (ไม่ใช่ moderated tag)
    unmoderated = [t for t in tags if not getattr(t, "moderated", False)]
    pool = unmoderated if unmoderated else tags
    # ลองหา tag ที่ชื่อคล้ายกับเควส/ทั่วไปก่อน
    preferred_keywords = ["เควส", "quest", "ทั่วไป", "general", "อื่นๆ", "other", "dm", "จอง", "ปกติ"]
    for keyword in preferred_keywords:
        for tag in pool:
            if keyword.lower() in tag.name.lower():
                return [tag]
    # ถ้าไม่มี tag ที่ตรงก็เลือก tag แรกในรายการ
    return [pool[0]]


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

        # 📝 เพิ่ม/อัปเดตบรรทัดประวัติการแก้ไขเวลา (เก็บแค่ครั้งล่าสุด แก้ได้หลายครั้ง)
        history_line = f"✏️ **แก้ไขเวลาล่าสุด:** {self.new_schedule.value} (โดย {interaction.user.display_name})"
        hist_lines = new_content.split('\n')
        hist_lines = [l for l in hist_lines if not l.startswith("✏️ **แก้ไขเวลาล่าสุด:**")]
        hist_lines.append(history_line)
        new_content = '\n'.join(hist_lines)

        try:
            await message.edit(content=new_content)
            await interaction.followup.send(
                "✅ แก้ไขเวลานัดหมายเรียบร้อยแล้ว!\n💡 สามารถแก้ไขได้อีกครั้งได้ตลอดเลยครับ",
                ephemeral=True
            )
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

    @discord.ui.button(
        label="✅ จบเควสแล้ว",
        style=discord.ButtonStyle.success,
        custom_id="quest:end_quest"
    )
    async def end_quest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 🔑 ตรวจสอบสิทธิ์: ใช้ได้เฉพาะคนที่มี DM Role (ยศที่กำหนด) เท่านั้น
        config = get_guild_config(interaction.guild_id)
        dm_role_id = config.get("dm_role_id")
        is_dm = bool(dm_role_id) and any(role.id == int(dm_role_id) for role in interaction.user.roles)

        if not is_dm:
            await interaction.response.send_message(
                "❌ ปุ่มนี้ใช้ได้เฉพาะ DM (ผู้ที่มียศที่กำหนด) เท่านั้น",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)  # ส่งข้อความแจ้งเป็นสาธารณะให้ทุกคนเห็น

        thread = interaction.channel
        old_name = thread.name

        # 🏷️ แปะ Tag "✅ จบเควสแล้ว" ให้กับกระทู้นี้ (หาหรือสร้างอัตโนมัติในห้อง Forum)
        forum = getattr(thread, "parent", None)
        if isinstance(forum, discord.ForumChannel):
            complete_tags = await _get_or_create_forum_tags(forum, ["✅ จบเควสแล้ว"])
            if complete_tags:
                current_tags = list(getattr(thread, "applied_tags", []) or [])
                existing_ids = {t.id for t in current_tags}
                for t in complete_tags:
                    if t.id not in existing_ids:
                        current_tags.append(t)
                try:
                    await thread.edit(applied_tags=current_tags[:5])
                except Exception:
                    pass

        # เพิ่ม prefix [✅ จบแล้ว] ในชื่อกระทู้ ถ้ายังไม่มี
        if not old_name.startswith("[✅ จบแล้ว]"):
            new_name = f"[✅ จบแล้ว] {old_name}"[:100]
            try:
                await thread.edit(name=new_name)
            except Exception:
                pass

        # ส่งข้อความแจ้งในกระทู้
        await interaction.followup.send(
            f"✅ **เควสจบเรียบร้อยแล้ว!** ขอบคุณที่ร่วมเล่นครับ 🎉\n(ปุ่มทั้งหมดจะถูกปิดใช้งาน)",
            ephemeral=False,
        )

        # 💡 ไม่ปิดปุ่ม — ปุ่มทั้งหมดคงอยู่กดได้ต่อ (ถ้าอนาคตอยากปิด ให้เอาโค้ดด้านล่างกลับมาใช้)
        # try:
        #     await interaction.message.edit(view=None)
        # except Exception:
        #     pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        await _handle_view_error(interaction, error, item)


# ============================================================
# 🏷️ ตัวเลือก Tag สำหรับตอนจองเควส (3 หัวข้อ) — สามารถแก้ไขรายการได้ตรงนี้
# ============================================================
QUEST_TYPE_OPTIONS = ["One Shot", "Short Adventure"]
PAYMENT_OPTIONS = ["Donation", "Payment"]
TIER_OPTIONS = ["Lv. 1-4", "Lv. 5-10", "Lv. 11-16", "Lv. 17-20"]


async def _get_or_create_forum_tags(forum_channel: discord.ForumChannel, tag_names: list) -> list:
    """หา Tag จากชื่อ ถ้าไม่มีให้สร้างใหม่อัตโนมัติ (ถ้าบอทมีสิทธิ์) คืน list ของ ForumTag (สูงสุด 5 อัน)"""
    result = []
    existing = {t.name.lower(): t for t in forum_channel.available_tags}
    for name in tag_names:
        key = str(name).lower().strip()
        if not key:
            continue
        if key in existing:
            result.append(existing[key])
        else:
            try:
                new_tag = await forum_channel.create_tag(name=str(name))
                result.append(new_tag)
                existing[key] = new_tag
            except (discord.Forbidden, discord.HTTPException):
                pass  # ไม่มีสิทธิ์สร้าง หรือสร้างไม่สำเร็จ ข้ามไป
    return result[:5]  # Discord จำกัดสูงสุด 5 tags ต่อกระทู้


# ============================================================
# 📋 View: เลือก Tag 3 หัวข้อตอนจองเควส (ขั้นตอนที่ 1 ก่อนกรอกเวลา)
# ============================================================
class TagSelectView(discord.ui.View):
    def __init__(self, quest_title: str, quest_description: str, recommended_level: str, image_url: str = None):
        super().__init__(timeout=300)  # หมดอายุใน 5 นาที
        self.quest_title = quest_title
        self.quest_description = quest_description
        self.recommended_level = recommended_level
        self.image_url = image_url
        self.selected_tags = []

    @discord.ui.select(
        placeholder="1. ประเภทเควส: One Shot / Short Adventure",
        options=[discord.SelectOption(label=opt, value=opt) for opt in QUEST_TYPE_OPTIONS],
        custom_id="quest:tag_type",
        min_values=1, max_values=1,
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_tags.append(select.values[0])
        select.placeholder = f"✅ {select.values[0]}"
        select.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="2. Payment: Donation / Payment",
        options=[discord.SelectOption(label=opt, value=opt) for opt in PAYMENT_OPTIONS],
        custom_id="quest:tag_payment",
        min_values=1, max_values=1,
    )
    async def select_payment(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_tags.append(select.values[0])
        select.placeholder = f"✅ {select.values[0]}"
        select.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="3. Level: Lv. 1-4 / Lv. 5-10 / Lv. 11-16 / Lv. 17-20",
        options=[discord.SelectOption(label=opt, value=opt) for opt in TIER_OPTIONS],
        custom_id="quest:tag_tier",
        min_values=1, max_values=1,
    )
    async def select_tier(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_tags.append(select.values[0])
        select.placeholder = f"✅ {select.values[0]}"
        select.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="ถัดไป → กรอกเวลา",
        style=discord.ButtonStyle.primary,
        custom_id="quest:tag_next",
    )
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.selected_tags) < 3:
            await interaction.response.send_message(
                f"⚠️ ยังเลือกไม่ครบ ({len(self.selected_tags)}/3 หัวข้อ) กรุณาแตะเลือกให้ครบทั้ง 3 ช่องก่อน",
                ephemeral=True,
            )
            return
        modal = BookingModal(
            self.quest_title, self.quest_description, self.recommended_level, self.image_url,
            selected_tags=self.selected_tags,
        )
        await interaction.response.send_modal(modal)


# ============================================================
# 🧩 Modal: หน้าต่างกรอกวันเวลาของผู้เล่นตอนจองครั้งแรก
# ============================================================
class BookingModal(discord.ui.Modal, title="จองเควส"):
    schedule = discord.ui.TextInput(
        label="วันเวลาที่สะดวก (ไม่บังคับ — ปล่อยว่างได้)",
        placeholder="เช่น 01/12/26 20.00 (ปล่อยว่างได้ถ้ายังไม่แน่ใจ)",
        style=discord.TextStyle.short,
        required=False,
        max_length=50,
    )

    def __init__(self, quest_title: str, quest_description: str, recommended_level: str, image_url: str = None, selected_tags: list = None):
        super().__init__()
        self.quest_title = quest_title
        self.quest_description = quest_description
        self.recommended_level = recommended_level
        self.image_url = image_url
        self.selected_tags = selected_tags or []

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

        # จัดการเวลา: ถ้าผู้เล่นไม่กรอกก็ไม่บังคับ ใช้ข้อความเริ่มต้น
        schedule_text = self.schedule.value.strip() if self.schedule.value else ""
        schedule_display = schedule_text if schedule_text else "⏳ ยังไม่ระบุเวลา (จะแจ้งภายหลัง)"

        content = (
            f"📥 **บันทึกการจองเควส:** {self.quest_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🙋 **ผู้เล่นที่จอง:** {interaction.user.mention}\n"
            f"🗓️ **วันเวลาที่สะดวก:** {schedule_display}\n"
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

        thread_name = f"{self.quest_title} - {interaction.user.display_name}"[:100]

        kwargs = {
            "name": thread_name,
            "content": content,
            "allowed_mentions": discord.AllowedMentions(users=[interaction.user]),
            "view": EditTimeView()
        }

        # 🏷️ ใช้ Tag ที่ผู้ใช้เลือกเอง (หา/สร้างในห้อง Forum อัตโนมัติ) ถ้าไม่มีให้บอทเลือกอัตโนมัติ
        if self.selected_tags:
            applied_tags = await _get_or_create_forum_tags(join_forum, self.selected_tags)
        else:
            applied_tags = []
        if not applied_tags:
            applied_tags = _pick_forum_tags(join_forum)
        if applied_tags:
            kwargs["applied_tags"] = applied_tags

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

        # ตรวจสอบว่าตั้งค่าห้อง Join เควสแล้วหรือไม่ ก่อนให้เลือก Tag
        config = get_guild_config(interaction.guild_id)
        if not config.get("quest_join_forum_id"):
            await interaction.response.send_message(
                "❌ เซิร์ฟเวอร์นี้ยังไม่ได้ตั้งค่าห้อง Join เควส — ให้ผู้ดูแลใช้คำสั่ง /setup ก่อน",
                ephemeral=True,
            )
            return

        # 📋 ขั้นตอนที่ 1: ให้ผู้ใช้เลือก Tag 3 หัวข้อจาก dropdown
        view = TagSelectView(quest_title, quest_description, recommended_level, image_url)
        await interaction.response.send_message(
            "📋 **ขั้นตอนที่ 1/2: เลือก Tag สำหรับเควสนี้**\n"
            "แตะเลือกให้ครบทั้ง 3 ช่อง แล้วกดปุ่ม **'ถัดไป → กรอกเวลา'**",
            view=view,
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        await _handle_view_error(interaction, error, item)


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
# 🧹 คำสั่งพิเศษ: ล้าง Slash Command ทั้งหมดแล้วซิงค์ใหม่ (แก้ CommandSignatureMismatch)
# ============================================================
@bot.command()
@commands.is_owner()
async def clear_sync(ctx):
    """ใช้เมื่อขึ้น error 'CommandSignatureMismatch' — ล้างคำสั่งเก่าทั้งหมดแล้วลงทะเบียนใหม่"""
    try:
        await ctx.send("🧹 กำลังล้าง Slash Command เก่าทั้งหมด...")
        # ล้างทุกคำสั่งใน guild นี้ (ใน memory) แล้ว sync เพื่อลบออกจาก Discord จริง
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        # คัดลอก global commands มาแล้ว sync ใหม่
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(
            f"✅ ล้างและซิงค์ใหม่สำเร็จ จำนวน {len(synced)} คำสั่ง\n"
            f"⏳ รอประมาณ 1-2 นาทีให้ Discord อัปเดต แล้วค่อยลองใช้ Slash Command ใหม่นะครับ"
        )
    except Exception as e:
        await ctx.send(f"❌ เกิดข้อผิดพลาด: {e}")


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

# ============================================================
# 🏷️ Autocomplete: แสดงรายการ Tag ของห้อง Forum ที่ผู้ใช้เลือกไว้ ให้เลือกเองได้
# ============================================================
async def autocomplete_quest_tags(interaction: discord.Interaction, current: str):
    """ดึงรายการ Tag จากห้อง Forum ที่ผู้ใช้เลือกไว้แล้ว มาแสดงเป็น dropdown ให้เลือกเอง"""
    quest_board = getattr(interaction.namespace, "quest_board", None)
    if not quest_board or not isinstance(quest_board, discord.ForumChannel):
        return []
    tags = quest_board.available_tags
    if not tags:
        return []
    # กรองตามที่ผู้ใช้พิมพ์ (ถ้ามี)
    if current:
        tags = [t for t in tags if current.lower() in t.name.lower()]
    # Discord จำกัดรายการ autocomplete สูงสุด 25 รายการ
    return [
        app_commands.Choice(name=tag.name[:100], value=str(tag.id))
        for tag in tags[:25]
    ]


@bot.tree.command(name="create_quest", description="สร้างเควสใหม่บนกระดานเควสพร้อมรูปภาพ (เฉพาะ DM)")
@app_commands.describe(
    quest_board="เลือกห้อง Forum กระดานเควสที่ต้องการลง (เช่น Tier 1, Tier 2 ฯลฯ)",
    title="ชื่อเควส",
    dm_name="ชื่อ DM ที่คุมเควสนี้ (พิมพ์ชื่อเองได้เลย)",
    description="คำอธิบายเควส",
    max_players="จำนวนผู้เล่นที่รับ",
    recommended_level="ระดับเลเวลที่แนะนำ (เช่น เลเวล 3, หรือ 1-4)",
    quest_tag="เลือก Tag สำหรับกระทู้นี้ (ไม่ใส่ก็ได้ บอทจะเลือกอัตโนมัติ)",
    image_file="อัปโหลดรูปภาพโดยตรง (ลากรูปใส่ช่องนี้)",
    image_url="หรือ วางลิงก์รูปภาพเว็บนอก (เช่น https://...)"
)
@app_commands.autocomplete(quest_tag=autocomplete_quest_tags)
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
        quest_tag: str = None,
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

    # 🏷️ เลือก tag: ถ้าผู้ใช้เลือกเองให้ใช้ของเขา ถ้าไม่บอทจะเลือกอัตโนมัติ
    if quest_tag:
        try:
            tag_id = int(quest_tag)
            chosen = discord.utils.get(board_forum.available_tags, id=tag_id)
            applied_tags = [chosen] if chosen else _pick_forum_tags(board_forum)
        except (ValueError, TypeError):
            applied_tags = _pick_forum_tags(board_forum)
    else:
        applied_tags = _pick_forum_tags(board_forum)
    if applied_tags:
        kwargs["applied_tags"] = applied_tags

    if file_to_send:
        kwargs["file"] = file_to_send

    await board_forum.create_thread(**kwargs)
    await interaction.followup.send(f"✅ สร้างเควสในห้อง {board_forum.mention} เรียบร้อยแล้ว", ephemeral=True)


# ============================================================
# 🚨 Error Handler สำหรับ Slash Command
# ============================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # ดึง error ตัวจริงออกมา (CommandInvokeError จะห่อหุ้ม error ที่เกิดจริง)
    original = getattr(error, "original", error)
    print(f"[Command Error] {type(original).__name__}: {original!r}")
    traceback.print_exception(type(original), original, original.__traceback__)

    if isinstance(error, app_commands.MissingRole):
        msg = "⛔ คุณไม่มีสิทธิ์ใช้คำสั่งนี้"
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "⛔ คำสั่งนี้ใช้ได้เฉพาะผู้ดูแลเซิร์ฟเวอร์ (มีสิทธิ์ Manage Server) เท่านั้น"
    elif isinstance(error, app_commands.CheckFailure):
        msg = f"⛔ {error}"
    elif isinstance(original, discord.Forbidden):
        msg = (
            "⛔ บอทไม่มีสิทธิ์ดำเนินการนี้\n"
            "สาเหตุที่พบบ่อย: บอทไม่มีสิทธิ์สร้าง/จัดการกระทู้ในห้องนั้นๆ "
            "ให้ตรวจสอบสิทธิ์ของบอทในห้อง Forum นั้น (คลิกขวาที่ห้อง → Edit Channel → Permissions)"
        )
    elif isinstance(original, discord.HTTPException):
        msg = f"❌ Discord API Error: {original.status} — {original.text}"
    else:
        error_type = type(original).__name__
        error_msg = str(original)[:200]
        msg = (
            f"❌ เกิดข้อผิดพลาด: `{error_type}`\n"
            f"รายละเอียด: {error_msg}\n\n"
            f"กรุณาส่งข้อความนี้ (พร้อม log ใน Terminal) ให้ผู้พัฒนาเพื่อตรวจสอบต่อ"
        )

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
