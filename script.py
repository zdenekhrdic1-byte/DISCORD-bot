import os
import io
import math
import base64
import asyncio
import threading
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, render_template_string, request, send_file

# --- NASTAVENÍ DISCORD BOTA ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

CHANNEL_ID = 1555810063652749324
GUILD_ID = 1555810062524612658

bot_loop = None

# Max velikost jednoho textového souboru (přibližně 8 MB pro jistotu a rychlost)
CHUNK_SIZE = 8 * 1024 * 1024

# --- NASTAVENÍ FLASK WEB SERVERU ---
app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Bot Web Control</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; margin-top: 50px; background-color: #2c2f33; color: white; }
        .box { background-color: #23272a; padding: 20px; border-radius: 10px; display: inline-block; margin: 10px; width: 340px; }
        button, input[type="submit"] { padding: 10px 20px; font-size: 16px; cursor: pointer; background-color: #5865F2; color: white; border: none; border-radius: 5px; margin-top: 10px; }
        button:hover, input[type="submit"]:hover { background-color: #4752C4; }
        input[type="file"] { margin-top: 10px; }
    </style>
</head>
<body>
    <h1>Lokalni Web pro Velke Soubory</h1>

    <div class="box">
        <h3>Poslat zprávu</h3>
        <form action="/say-hi" method="post">
            <button type="submit">make bot say hi</button>
        </form>
    </div>

    <div class="box">
        <h3>Uložit velký soubor (rozsekání na části)</h3>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="file" required><br>
            <input type="submit" value="Rozsekat a uložit na Discord">
        </form>
    </div>

    {% if message %}
        <p style="color: #57F287; font-weight: bold;">{{ message }}</p>
    {% endif %}
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML_PAGE)


@app.route("/say-hi", methods=["POST"])
def say_hi():
    async def send_msg():
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send("hi")

    if bot_loop:
        asyncio.run_coroutine_threadsafe(send_msg(), bot_loop)

    return render_template_string(HTML_PAGE, message="Zpráva 'hi' byla poslána do Discordu!")


# ULOŽENÍ VELKÉHO SOUBORU
@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return render_template_string(HTML_PAGE, message="Nebyl vybrán žádný soubor!")

    file = request.files["file"]
    if file.filename == "":
        return render_template_string(HTML_PAGE, message="Nebyl vybrán žádný soubor!")

    if file:
        file_bytes = file.read()
        filename = file.filename

        # Převedení na Base64 text
        b64_data = base64.b64encode(file_bytes).decode('utf-8')

        # Rozsekání Base64 textu na části podle CHUNK_SIZE
        total_chunks = math.ceil(len(b64_data) / CHUNK_SIZE)
        chunks = [b64_data[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE] for i in range(total_chunks)]

        async def process_and_send():
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                return

            category = discord.utils.get(guild.categories, name="saves")
            if not category:
                category = await guild.create_category("saves")

            now = datetime.now()
            channel_name = now.strftime("%d-%m-%Y")
            time_heading = now.strftime("%H:%M:%S")

            target_channel = discord.utils.get(category.text_channels, name=channel_name)
            if not target_channel:
                target_channel = await category.create_text_channel(name=channel_name)

            # Příprava souborů pro odeslání
            discord_files = []
            for idx, chunk in enumerate(chunks):
                file_obj = discord.File(
                    io.BytesIO(chunk.encode('utf-8')),
                    filename=f"part_{idx + 1}_of_{total_chunks}.txt"
                )
                discord_files.append(file_obj)

            # Odeslání do jediné zprávy s aktualizovaným časem v nadpisu
            message_content = f"## Čas uložení: {time_heading}\nPůvodní název: **{filename}**\nPočet částí: `{total_chunks}`"

            # Pokud se všechny části vejdou do jedné zprávy (max 10 příloh na zprávu v Discordu)
            if len(discord_files) <= 10:
                await target_channel.send(content=message_content, files=discord_files)
            else:
                # Pokud má soubor více než 10 částí, pošle první zprávu a další nahrává postupně
                msg = await target_channel.send(content=message_content, files=discord_files[:10])

        if bot_loop:
            asyncio.run_coroutine_threadsafe(process_and_send(), bot_loop)

        return render_template_string(HTML_PAGE,
                                      message=f"Soubor '{filename}' byl úspěšně rozsekán na {total_chunks} částí a uložen!")


# STAŽENÍ A SPOJENÍ VŠECH ČÁSTÍ ZPĚT
@app.route("/download/<channel_name>/<cas>")
def download_file(channel_name, cas):
    file_data = {"bytes": None, "filename": None}

    async def fetch_and_rebuild():
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        category = discord.utils.get(guild.categories, name="saves")
        if not category:
            return

        channel = discord.utils.get(category.text_channels, name=channel_name)
        if not channel:
            return

        async for message in channel.history(limit=100):
            if f"Čas uložení: {cas}" in message.content and message.attachments:
                # Načtení názvu souboru
                orig_filename = "stazeny_soubor"
                for line in message.content.split("\n"):
                    if "Původní název:" in line:
                        orig_filename = line.split("**")[1]

                # Seřazení částí podle názvu part_1, part_2...
                attachments = sorted(message.attachments, key=lambda x: x.filename)

                full_b64 = ""
                for attachment in attachments:
                    chunk_text = (await attachment.read()).decode('utf-8')
                    full_b64 += chunk_text

                # Převedení celého Base64 zpět na původní bajty
                original_bytes = base64.b64decode(full_b64)

                file_data["bytes"] = original_bytes
                file_data["filename"] = orig_filename
                break

    if bot_loop:
        future = asyncio.run_coroutine_threadsafe(fetch_and_rebuild(), bot_loop)
        future.result()

    if file_data["bytes"] and file_data["filename"]:
        return send_file(
            io.BytesIO(file_data["bytes"]),
            as_attachment=True,
            download_name=file_data["filename"]
        )
    else:
        return "Soubor s tímto časem nebyl v Discordu nalezen.", 404


def run_web():
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


# --- DISCORD BOT EVENTS A COMMANDS ---
@bot.event
async def on_ready():
    global bot_loop
    bot_loop = asyncio.get_running_loop()

    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)

    print("yes i am online")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("yes i am online")


@bot.tree.command(name="load", description="Otevře lokální web a připraví privátní kategorii")
async def load(interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    category = discord.utils.get(guild.categories, name="saves")
    if not category:
        category = await guild.create_category("saves", overwrites=overwrites)
    else:
        await category.set_permissions(user, read_messages=True, send_messages=True)

    web_url = "http://127.0.0.1:5000"
    await interaction.response.send_message(
        f"Privátní kategorie `saves` je připravena!\nTvoji lokální stránku otevřeš zde: {web_url}", ephemeral=True)


@bot.tree.command(name="save", description="Vygeneruje odkaz ke stažení zrekonstruovaného souboru")
@app_commands.describe(cas="Čas uložení souboru ve formátu HH:MM:SS (např. 07:10:18)")
async def save(interaction: discord.Interaction, cas: str):
    channel_name = interaction.channel.name
    download_link = f"http://127.0.0.1:5000/download/{channel_name}/{cas}"

    await interaction.response.send_message(
        content=f"Kliknutím na tento odkaz stáhneš složený soubor z času **{cas}**:\n{download_link}",
        ephemeral=True
    )


if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    bot.run("MTU1NTgxNDkxMjYzNzI4MDM0Ng.GG0hBA.ef7WAo3P8y2Hmpy_bdLOyX7pkEwNqri81S4RLI")