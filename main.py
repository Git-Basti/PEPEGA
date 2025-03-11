from typing import Final
import os
import json
import asyncio
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta

# Logging-Konfiguration
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('BOT-MAIN')

# ENV Variablen laden
load_dotenv()
TOKEN: Final[str] = os.getenv('DISCORD_TOKEN')
GUILD_ID: Final[int] = int(os.getenv('GUILD_ID')) if os.getenv('GUILD_ID') else None
DATA_FILE = "event_data.json"

# Bot-Klasse
class Client(commands.Bot):
    async def on_ready(self):
        log.info(f'✅ Eingeloggt als {self.user}!')
        try:
            guild = discord.Object(id=GUILD_ID)
            synced = await self.tree.sync(guild=guild)
            log.info(f'🔄 Synced {len(synced)} Commands mit Guild {guild.id}')
        except Exception as e:
            log.error(f'❌ Fehler beim Sync: {e}')
        check_events.start()
        data = load_data()
        for event_id, event in data["events"].items():
            self.add_view(EventButtons(event_id))


intents = discord.Intents.default()
intents.message_content = True
client = Client(command_prefix='!', intents=intents)

# Datenverwaltung
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"events": {}, "admins": [], "moderators": []}, f)

def load_data():
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


# --Event--
class EventModalBasic(discord.ui.Modal, title="Event-Erstellung (1/2)"):
    def __init__(self):
        super().__init__(title="Event-Erstellung (1/2)")
        self.title_input = discord.ui.TextInput(label="Event-Titel", required=True)
        self.date_input = discord.ui.TextInput(label="Datum (DD.MM.JJJJ)", required=True)
        self.time_input = discord.ui.TextInput(label="Uhrzeit (HH:MM)", required=True)
        self.add_item(self.title_input)
        self.add_item(self.date_input)
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        basic_data = {
            "title": self.title_input.value,
            "date": self.date_input.value,
            "time": self.time_input.value,
        }
        view = ContinueView(basic_data)
        await interaction.response.send_message("Bitte fahre fort und ergänze weitere Details:", view=view, ephemeral=True)


class ContinueView(discord.ui.View):
    def __init__(self, basic_data: dict):
        super().__init__()
        self.basic_data = basic_data

    @discord.ui.button(label="Weiter", style=discord.ButtonStyle.primary)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EventModalDetails(self.basic_data))

class EventModalDetails(discord.ui.Modal, title="Event-Erstellung (2/2)"):
    def __init__(self, basic_data: dict):
        self.basic_data = basic_data
        super().__init__(title="Event-Erstellung (2/2)")
        self.game_title = discord.ui.TextInput(label="Spiel-Titel", required=True)
        self.max_players = discord.ui.TextInput(label="Max. Spieleranzahl", required=True)
        self.duration = discord.ui.TextInput(label="Rundendauer in Minuten", required=False)
        self.description = discord.ui.TextInput(label="Beschreibung", style=discord.TextStyle.paragraph, required=True)
        self.rulebook = discord.ui.TextInput(label="Regelwerk / Steam Link", required=False)
        self.add_item(self.game_title)
        self.add_item(self.max_players)
        self.add_item(self.duration)
        self.add_item(self.description)
        self.add_item(self.rulebook)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            event_datetime = datetime.strptime(
                self.basic_data["date"].strip() + " " + self.basic_data["time"].strip(),
                "%d.%m.%Y %H:%M"
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ Ungültiges Datums- oder Zeitformat! Bitte nutze DD.MM.JJJJ für das Datum und HH:MM für die Uhrzeit.",
                ephemeral=True
            )
            return

        now = datetime.now()
        if event_datetime <= now:
            await interaction.response.send_message(
                "❌ Das angegebene Datum/Zeit liegt in der Vergangenheit. Bitte eine zukünftige Zeit wählen.",
                ephemeral=True
            )
            return

        try:
            max_players = int(self.max_players.value)
        except ValueError:
            await interaction.response.send_message("❌ Max. Spieleranzahl muss eine Zahl sein.", ephemeral=True)
            return

        if self.duration.value.strip():
            try:
                duration_minutes = int(self.duration.value)
            except ValueError:
                await interaction.response.send_message("❌ Rundendauer muss eine Zahl (Minuten) sein.", ephemeral=True)
                return
            end_datetime = event_datetime + timedelta(minutes=duration_minutes)
        else:
            duration_minutes = None

        data = load_data()
        event_id = str(interaction.id)
        event_data = {
            "title": self.basic_data["title"],
            "time": event_datetime.strftime("%d.%m.%Y %H:%M"),
            "game": self.game_title.value,
            "max_players": max_players,
            "description": self.description.value,
            "rulebook": self.rulebook.value,
            "participants": {"yes": [], "maybe": [], "no": []},
            "message_id": None,
            "channel_id": interaction.channel.id,
        }
        if duration_minutes is not None:
            event_data["end_time"] = end_datetime.strftime("%d.%m.%Y %H:%M")
        data["events"][event_id] = event_data
        save_data(data)

        formatted_datetime = event_datetime.strftime("%d.%m.%Y %H:%M")
        relative_timestamp = f"<t:{int(event_datetime.timestamp())}:R>"
        embed = discord.Embed(title=self.basic_data["title"], color=discord.Color.blue())
        embed.add_field(name="📅 Datum & Uhrzeit", value=f"{formatted_datetime} \n {relative_timestamp}", inline=True)
        embed.add_field(name="🎮 Spiel", value=self.game_title.value, inline=True)
        embed.add_field(name="👥 Max. Spieler", value="        " + str(max_players), inline=True)
        if duration_minutes is not None:
            embed.add_field(name="⏳ Dauer", value=f"{duration_minutes} Minuten", inline=True)
        embed.add_field(name="📜 Beschreibung", value=self.description.value, inline=False)
        if self.rulebook.value:
            embed.add_field(name="📌 Regelwerk / Link", value=self.rulebook.value, inline=False)
        embed.add_field(name="✅ Zusagen", value="Noch keine", inline=True)
        embed.add_field(name="⚠️ Vielleicht", value="Noch keine", inline=True)
        embed.add_field(name="❌ Absagen", value="Noch keine", inline=True)

        view = EventButtons(event_id)
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        data = load_data()
        data["events"][event_id]["message_id"] = message.id
        save_data(data)


class EventButtons(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)  # persistent
        self.event_id = event_id

        # Erstelle Buttons mit eindeutigen custom_ids
        join_button = discord.ui.Button(label="✅ Teilnehmen", style=discord.ButtonStyle.green,
                                        custom_id=f"join_{event_id}")
        maybe_button = discord.ui.Button(label="⚠️ Vielleicht", style=discord.ButtonStyle.blurple,
                                         custom_id=f"maybe_{event_id}")
        decline_button = discord.ui.Button(label="❌ Absagen", style=discord.ButtonStyle.red,
                                           custom_id=f"decline_{event_id}")

        join_button.callback = self.join_event
        maybe_button.callback = self.maybe_event
        decline_button.callback = self.decline_event

        self.add_item(join_button)
        self.add_item(maybe_button)
        self.add_item(decline_button)

    async def join_event(self, interaction: discord.Interaction):
        await self._handle_participation(interaction, "yes")

    async def maybe_event(self, interaction: discord.Interaction):
        await self._handle_participation(interaction, "maybe")

    async def decline_event(self, interaction: discord.Interaction):
        await self._handle_participation(interaction, "no")

    async def _handle_participation(self, interaction: discord.Interaction, choice: str):
        data = load_data()
        event = data["events"].get(self.event_id)
        if not event:
            await interaction.response.send_message("❌ Event nicht gefunden!", ephemeral=True)
            return
        user_id = interaction.user.id
        participants = event["participants"]

        for category in ["yes", "maybe", "no"]:
            if user_id in participants[category]:
                participants[category].remove(user_id)

        if choice == "yes" and len(participants["yes"]) >= event["max_players"]:
            choice = "maybe"
            await interaction.response.send_message("ℹ️ Event ist voll – du wurdest auf die Warteliste gesetzt.", ephemeral=True)
        if user_id not in participants[choice]:
            participants[choice].append(user_id)

        if choice != "yes" and len(participants["yes"]) < event["max_players"] and participants["maybe"]:
            first_waiting = participants["maybe"].pop(0)
            if first_waiting != user_id:
                participants["yes"].append(first_waiting)
            else:
                participants["maybe"].insert(0, first_waiting)
        event["participants"] = participants
        data["events"][self.event_id] = event
        save_data(data)

        try:
            event_datetime = datetime.strptime(event["time"], "%d.%m.%Y %H:%M")
        except ValueError:
            await interaction.response.send_message("❌ Fehler beim Parsen des Datums.", ephemeral=True)
            return
        formatted_datetime = event_datetime.strftime("%d.%m.%Y %H:%M")
        relative_timestamp = f"<t:{int(event_datetime.timestamp())}:R>"
        embed = discord.Embed(title=event["title"], color=discord.Color.blue())
        embed.add_field(name="📅 Datum & Uhrzeit", value=f"{formatted_datetime} \n {relative_timestamp}", inline=True)
        embed.add_field(name="🎮 Spiel", value=event["game"], inline=True)
        embed.add_field(name="👥 Max. Spieler", value="        " + str(event["max_players"]), inline=True)
        if "end_time" in event:
            duration_minutes = int((datetime.strptime(event["end_time"], "%d.%m.%Y %H:%M") - event_datetime).total_seconds() // 60)
            embed.add_field(name="⏳ Dauer", value=f"{duration_minutes} Minuten", inline=True)
        embed.add_field(name="📜 Beschreibung", value=event["description"], inline=False)
        if event["rulebook"]:
            embed.add_field(name="📌 Regelwerk / Link", value=event["rulebook"], inline=False)
        yes_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["yes"]]) or "Keine"
        maybe_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["maybe"]]) or "Keine"
        no_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["no"]]) or "Keine"
        embed.add_field(name="✅ Zusagen", value=yes_mentions, inline=True)
        embed.add_field(name="⚠️ Vielleicht", value=maybe_mentions, inline=True)
        embed.add_field(name="❌ Absagen", value=no_mentions, inline=True)
        await interaction.response.edit_message(embed=embed)


@client.tree.command(name="event", description="Erstelle ein neues Event", guild=discord.Object(id=GUILD_ID))
async def create_event(interaction: discord.Interaction):
    await interaction.response.send_modal(EventModalBasic())

# --Event Ende--


@client.tree.command(name="set_permissions", description="Verwalte die Bot Rechte. Nutze: `admin`, `moderator`", guild=discord.Object(id=GUILD_ID))
async def set_permissions(interaction: discord.Interaction, user: discord.User, role: str):
    data = load_data()
    # Nur Admins dürfen Berechtigungen setzen
    if interaction.user.id not in data["admins"]:
        await interaction.response.send_message("❌ Du hast keine Berechtigung, diesen Befehl zu verwenden.", ephemeral=True)
        return
    role_lower = role.lower()
    if role_lower == "admin":
        if user.id in data["admins"]:
            await interaction.response.send_message(f"{user.mention} ist bereits Admin.", ephemeral=True)
            return
        if user.id in data["moderators"]:
            data["moderators"].remove(user.id)
        data["admins"].append(user.id)
        role_name = "Admin"
    elif role_lower == "moderator":
        if user.id in data["admins"]:
            await interaction.response.send_message(f"{user.mention} ist bereits Admin.", ephemeral=True)
            return
        if user.id in data["moderators"]:
            await interaction.response.send_message(f"{user.mention} ist bereits Moderator.", ephemeral=True)
            return
        data["moderators"].append(user.id)
        role_name = "Moderator"
    else:
        await interaction.response.send_message("❌ Ungültige Rolle! Nutze 'admin' oder 'moderator'.", ephemeral=True)
        return
    save_data(data)
    await interaction.response.send_message(f"✅ {user.mention} wurde als {role_name} hinzugefügt!", ephemeral=True)


@tasks.loop(minutes=1)
async def check_events():
    now = datetime.now()
    data = load_data()
    for event_id, event in list(data["events"].items()):
        event_time = datetime.strptime(event["time"], "%d.%m.%Y %H:%M")

        if now >= event_time - timedelta(hours=1) and not event.get("reminder_sent"):
            channel = client.get_channel(event["channel_id"])
            try:
                message = await channel.fetch_message(event["message_id"])
            except:
                message = None
            if message:
                event_details_link = f"[Event Details]({message.jump_url})"
            else:
                event_details_link = ''
            embed = discord.Embed(
                title="Event Erinnerung",
                description=f"⏳ In einer Stunde startet **{event['title']}**!\n{event_details_link}",
                color=discord.Color.blue()
            )
            await channel.send(embed=embed)
            event["reminder_sent"] = True

        if now >= event_time:
            channel = client.get_channel(event["channel_id"])
            try:
                msg = await channel.fetch_message(event["message_id"])
                # Option 1: Buttons deaktivieren statt entfernen
                disabled_view = EventButtons(event_id)
                for child in disabled_view.children:
                    child.disabled = True
                await msg.edit(view=disabled_view)
            except discord.NotFound:
                log.warning("Nachricht nicht gefunden, daher keine Buttons entfernt.")
            except Exception as e:
                log.error(f"Fehler beim Entfernen der Buttons: {e}")

            embed = discord.Embed(
                title="Event Start",
                description=f"🎮 **{event['title']}** startet jetzt!",
                color=discord.Color.green()
            )
            await channel.send(embed=embed)
            del data["events"][event_id]
    save_data(data)


# Bot starten
client.run(TOKEN)
