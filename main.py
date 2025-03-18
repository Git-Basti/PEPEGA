from typing import Final
import os
import json
import asyncio
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import aiohttp
import base64

# Logging-Konfiguration
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('BOT-MAIN')

# ENV Variablen laden
load_dotenv()
TOKEN: Final[str] = os.getenv('DISCORD_TOKEN')
GUILD_ID: Final[int] = int(os.getenv('GUILD_ID')) if os.getenv('GUILD_ID') else None
DATA_FILE = "event_data.json"

# Bot-Initialisierung
intents = discord.Intents.default()
intents.message_content = True
client = commands.Bot(command_prefix='!', intents=intents)

# Datenverwaltung
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"events": {}, "admins": [], "moderators": [], "event_channel_id": None}, f)

def load_data():
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- Persistent View für Event-Buttons ---
class EventButtons(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)  # persistent: kein Timeout
        self.event_id = event_id

        # Buttons manuell erstellen, mit festen custom_ids
        join_button = discord.ui.Button(
            label="✅ Teilnehmen",
            style=discord.ButtonStyle.green,
            custom_id=f"join_{event_id}"
        )
        join_button.callback = self.join_event

        maybe_button = discord.ui.Button(
            label="⚠️ Vielleicht",
            style=discord.ButtonStyle.blurple,
            custom_id=f"maybe_{event_id}"
        )
        maybe_button.callback = self.maybe_event

        decline_button = discord.ui.Button(
            label="❌ Absagen",
            style=discord.ButtonStyle.red,
            custom_id=f"decline_{event_id}"
        )
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

        # Entferne den User aus allen Kategorien
        for category in ["yes", "maybe", "no", "waiting"]:
            if user_id in participants[category]:
                participants[category].remove(user_id)

        if choice == "yes":
            if len(participants["yes"]) >= event["max_players"]:
                # Wenn das Event voll ist, in die neue Kategorie "waiting" einfügen
                if user_id not in participants["waiting"]:
                    participants["waiting"].append(user_id)
                event["participants"] = participants
                data["events"][self.event_id] = event
                save_data(data)
                await interaction.response.send_message("ℹ️ Event ist voll – du wurdest auf die Warteliste gesetzt.", ephemeral=True)
                return
            else:
                if user_id not in participants["yes"]:
                    participants["yes"].append(user_id)
        else:
            if user_id not in participants[choice]:
                participants[choice].append(user_id)

        event["participants"] = participants
        data["events"][self.event_id] = event
        save_data(data)

        # Aktualisiere das Embed
        try:
            event_datetime = datetime.strptime(event["time"], "%d.%m.%Y %H:%M")
            local_tz = datetime.now().astimezone().tzinfo
            event_datetime = event_datetime.replace(tzinfo=local_tz)
        except ValueError:
            await interaction.response.send_message("❌ Fehler beim Parsen des Datums.", ephemeral=True)
            return

        formatted_datetime = event_datetime.strftime("%d.%m.%Y %H:%M")
        relative_timestamp = f"<t:{int(event_datetime.timestamp())}:R>"
        embed = discord.Embed(title=event["title"], color=discord.Color.blue())
        embed.add_field(name="📅 Datum & Uhrzeit", value=f"{formatted_datetime}\n{relative_timestamp}", inline=True)
        embed.add_field(name="🎮 Spiel", value=event["game"], inline=True)
        embed.add_field(name="👥 Max. Spieler", value=str(event["max_players"]), inline=True)
        if "end_time" in event:
            duration_minutes = int((datetime.strptime(event["end_time"], "%d.%m.%Y %H:%M").replace(
                tzinfo=local_tz) - event_datetime).total_seconds() // 60)
            embed.add_field(name="⏳ Dauer", value=f"{duration_minutes} Minuten", inline=True)
        embed.add_field(name="📜 Beschreibung", value=event["description"], inline=False)
        if event["rulebook"]:
            embed.add_field(name="📌 Regelwerk / Link", value=event["rulebook"], inline=False)
        yes_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["yes"]]) or "Keine"
        maybe_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["maybe"]]) or "Keine"
        waiting_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["waiting"]]) or "Keine"
        no_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["no"]]) or "Keine"
        embed.add_field(name="✅ Zusagen", value=yes_mentions, inline=True)
        embed.add_field(name="⚠️ Vielleicht", value=maybe_mentions, inline=True)
        embed.add_field(name="❌ Absagen", value=no_mentions, inline=True)
        # Optional: Im Update-Embed kann man die Warteliste auch anzeigen, hier erfolgt dies nicht zwingend
        await interaction.response.edit_message(embed=embed)

# --- Modale zur Event-Erstellung ---

# Erster Schritt: Grunddaten erfassen
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

# View, um den Übergang zum nächsten Schritt zu ermöglichen
class ContinueView(discord.ui.View):
    def __init__(self, basic_data: dict):
        super().__init__(timeout=None)
        self.basic_data = basic_data

    @discord.ui.button(label="Weiter", style=discord.ButtonStyle.primary, custom_id="continue_event")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EventModalDetails(self.basic_data))

# Zweiter Schritt: Weitere Event-Daten erfassen und Event anlegen
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
            # Mache das Datum "aware" indem du die lokale Zeitzone setzt
            local_tz = datetime.now().astimezone().tzinfo
            event_datetime = event_datetime.replace(tzinfo=local_tz)
        except ValueError:
            await interaction.response.send_message(
                "❌ Ungültiges Datums- oder Zeitformat! Bitte nutze DD.MM.JJJJ für das Datum und HH:MM für die Uhrzeit.",
                ephemeral=True
            )
            return

        now = datetime.now().astimezone()
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
        # Teilnehmer-Datenstruktur mit neuer Kategorie "waiting" initialisieren
        event_data = {
            "title": self.basic_data["title"],
            "time": event_datetime.strftime("%d.%m.%Y %H:%M"),
            "game": self.game_title.value,
            "max_players": max_players,
            "description": self.description.value,
            "rulebook": self.rulebook.value,
            "participants": {"yes": [], "maybe": [], "no": [], "waiting": []},
            "message_id": None,
            "channel_id": interaction.channel.id  # Speichere den Channel, in dem das Event erstellt wurde
        }
        if duration_minutes is not None:
            event_data["end_time"] = (event_datetime + timedelta(minutes=duration_minutes)).strftime("%d.%m.%Y %H:%M")
        data["events"][event_id] = event_data
        save_data(data)

        # Sende zuerst die Custom Event Nachricht, um den Nachrichtenlink (jump_url) zu erhalten
        view = EventButtons(event_id)
        await interaction.response.send_message(embed=self._build_embed(event_data, event_datetime, duration_minutes), view=view)
        message = await interaction.original_response()
        message_link = message.jump_url

        # Erstelle nun den nativen Discord-Event – unter Verwendung des Nachrichtenlinks
        guild = interaction.guild
        start_time = event_datetime
        end_time = start_time + timedelta(hours=2)
        event_name = event_data["title"]
        base_event_description = f"{event_data['game']}\n\n{event_data['description']}"
        participant_counts = "✅ Zusagen: 0\n⚠️ Vielleicht: 0\n❌ Absagen: 0"
        updated_event_description = f"{base_event_description}\n\n{participant_counts}\n\n\nEventdetails and Anmeldelink: {message_link}"

        cover_image_url = "https://cdn.discordapp.com/attachments/740231955731316796/1348899987785781258/Banner_fur_Events.png?ex=67d12482&is=67cfd302&hm=2b2dbede4f5e00f07da8346eccb2780bd2a06d976051b7371e731d452248b998&"
        async with aiohttp.ClientSession() as session:
            async with session.get(cover_image_url) as resp:
                if resp.status == 200:
                    image_bytes = await resp.read()
                    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
                    image_data = f"data:image/png;base64,{image_base64}"
                else:
                    image_data = None

        params = {
            "name": event_name,
            "start_time": start_time,
            "end_time": end_time,
            "privacy_level": discord.PrivacyLevel.guild_only,
            "entity_type": discord.EntityType.external,
            "description": updated_event_description,
            "location": message_link
        }
        if image_data is not None:
            params["image"] = image_data

        try:
            scheduled_event = await guild.create_scheduled_event(**params)
        except Exception as e:
            log.error(f"Fehler beim Erstellen des Discord-Events: {e}")
            scheduled_event = None

        if scheduled_event:
            event_link_native = f"https://discord.com/events/{guild.id}/{scheduled_event.id}"
        else:
            event_link_native = "Fehler beim Erstellen des Discord-Events."

        # Aktualisiere Eventdaten mit Nachrichten-ID und nativen Event-Link
        data = load_data()
        data["events"][event_id]["message_id"] = message.id
        data["events"][event_id]["discord_event_link"] = event_link_native
        data["events"][event_id]["discord_event_id"] = scheduled_event.id if scheduled_event else None
        data["events"][event_id]["message_link"] = message.jump_url
        save_data(data)

        # Sende den nativen Event-Link in den konfigurierten Channel, falls gesetzt
        event_channel_id = data.get("event_channel_id")
        if event_channel_id:
            event_channel = guild.get_channel(event_channel_id)
            if event_channel:
                await event_channel.send(f"Neues Discord-Event erstellt: {event_link_native}")
        else:
            log.warning("Kein Event-Channel in den Daten hinterlegt.")

    def _build_embed(self, event_data, event_datetime, duration_minutes):
        formatted_datetime = event_datetime.strftime("%d.%m.%Y %H:%M")
        relative_timestamp = f"<t:{int(event_datetime.timestamp())}:R>"
        embed = discord.Embed(title=event_data["title"], color=discord.Color.blue())
        embed.add_field(name="📅 Datum & Uhrzeit", value=f"{formatted_datetime}\n{relative_timestamp}", inline=True)
        embed.add_field(name="🎮 Spiel", value=event_data["game"], inline=True)
        embed.add_field(name="👥 Max. Spieler", value=str(event_data["max_players"]), inline=True)
        if duration_minutes is not None:
            embed.add_field(name="⏳ Dauer", value=f"{duration_minutes} Minuten", inline=True)
        embed.add_field(name="📜 Beschreibung", value=event_data["description"], inline=False)
        if event_data["rulebook"]:
            embed.add_field(name="📌 Regelwerk / Link", value=event_data["rulebook"], inline=False)
        embed.add_field(name="✅ Zusagen", value="Noch keine", inline=True)
        embed.add_field(name="⚠️ Vielleicht", value="Noch keine", inline=True)
        embed.add_field(name="❌ Absagen", value="Noch keine", inline=True)
        return embed

# --- Slash-Befehle ---

@client.tree.command(name="event", description="Erstelle ein neues Event", guild=discord.Object(id=GUILD_ID))
async def create_event(interaction: discord.Interaction):
    await interaction.response.send_modal(EventModalBasic())

@client.tree.command(name="set_permissions", description="Verwalte die Bot Rechte. Nutze: `admin`, `moderator`", guild=discord.Object(id=GUILD_ID))
async def set_permissions(interaction: discord.Interaction, user: discord.User, role: str):
    data = load_data()
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

@client.tree.command(name="set_event_channel", description="Setzt den Channel, in dem Discord-Event-Links gepostet werden", guild=discord.Object(id=GUILD_ID))
async def set_event_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    data = load_data()
    if interaction.user.id not in data["admins"]:
        await interaction.response.send_message("❌ Du hast keine Berechtigung, diesen Befehl zu verwenden.", ephemeral=True)
        return

    data["event_channel_id"] = channel.id
    save_data(data)
    await interaction.response.send_message(f"✅ Der Event Channel wurde auf {channel.mention} gesetzt.", ephemeral=True)

# --- Task: Überprüfung und Verwaltung von Events ---
@tasks.loop(minutes=1)
async def check_events():
    now = datetime.now().astimezone()
    guild = client.get_guild(GUILD_ID)
    data = load_data()
    for event_id, event in list(data["events"].items()):
        # Aktualisiere die native Eventbeschreibung
        try:
            yes_count = len(event["participants"]["yes"])
            maybe_count = len(event["participants"]["maybe"])
            no_count = len(event["participants"]["no"])
            waiting_count = len(event["participants"]["waiting"])
            participant_counts = f"✅ Zusagen: {yes_count}\n⚠️ Vielleicht: {maybe_count}\n❌ Absagen: {no_count}\n⏳ Warteliste: {waiting_count}"
            base_event_description = f"{event['game']}\n\n{event['description']}"
            message_link = event.get("message_link", "")
            updated_event_description = f"{base_event_description}\n\n{participant_counts}\n\nEventdetails and Anmeldelink: {message_link}"

            discord_event = await guild.fetch_scheduled_event(event["discord_event_id"])
            await discord_event.edit(description=updated_event_description)
        except Exception as e:
            log.error(f"Fehler beim Aktualisieren des nativen Discord-Events in der Loop: {e}")

        # Bestehende Logik für Erinnerungen und Start...
        try:
            event_time = datetime.strptime(event["time"], "%d.%m.%Y %H:%M")
            local_tz = datetime.now().astimezone().tzinfo
            event_time = event_time.replace(tzinfo=local_tz)
        except ValueError:
            continue

        if now >= event_time - timedelta(hours=1) and not event.get("reminder_sent"):
            channel = client.get_channel(event["channel_id"])
            try:
                message = await channel.fetch_message(event["message_id"])
            except:
                message = None
            event_details_link = f"[Event Details]({message.jump_url})" if message else ''
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
                disabled_view = EventButtons(event_id)
                for child in disabled_view.children:
                    child.disabled = True
                await msg.edit(view=disabled_view)
            except discord.NotFound:
                log.warning("Nachricht nicht gefunden, daher keine Buttons entfernt.")
            except Exception as e:
                log.error(f"Fehler beim Entfernen der Buttons: {e}")

            # Erstelle ein Embed für den Event-Start inklusive der Warteliste (Kategorie "waiting")
            yes_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["yes"]]) or "Keine"
            waiting_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["waiting"]]) or "Keine"
            no_mentions = ', '.join([f'<@{uid}>' for uid in event["participants"]["no"]]) or "Keine"
            embed = discord.Embed(
                title="Event Start",
                description=f"🎮 **{event['title']}** startet jetzt!",
                color=discord.Color.green()
            )
            embed.add_field(name="✅ Zusagen", value=yes_mentions, inline=True)
            embed.add_field(name="⏳ Warteliste", value=waiting_mentions, inline=True)
            await channel.send(embed=embed)
            del data["events"][event_id]
    save_data(data)

@client.event
async def on_ready():
    log.info(f'✅ Eingeloggt als {client.user}!')
    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await client.tree.sync(guild=guild)
        log.info(f'🔄 Synced {len(synced)} Commands mit Guild {guild.id}')
    except Exception as e:
        log.error(f'❌ Fehler beim Sync: {e}')
    # Registriere persistent Views für alle aktiven Events
    data = load_data()
    for event_id in data["events"]:
        client.add_view(EventButtons(event_id))
    check_events.start()

client.run(TOKEN)
