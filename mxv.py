import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient, functions
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PhoneNumberInvalidError, 
    FloodWaitError, PasswordHashInvalidError,
    ChannelPrivateError, ApiIdInvalidError, PhoneCodeExpiredError
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest, GetFullChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetFullChatRequest
from telethon.tl.functions.phone import JoinGroupCallRequest, GetGroupCallRequest, LeaveGroupCallRequest
from telethon.tl.types import InputGroupCall, InputPeerChannel, DataJSON, InputPeerUser
import os
import json
import re
import time
from datetime import datetime

BOT_TOKEN = "8292498684:AAHCjJxLNn16PTyPcWeaXMkYWWMecg_zuG0"
API_ID = 38524920
API_HASH = "08290d2c8cbd436f3b1c16f082777620"

CHANNELS = [-1003689343135, -1003604665519, -1003697364580]
CHANNEL_LINKS = [
    "https://t.me/+0gWpc_0xwoE0NWRl",
    "https://t.me/+fe2Q3mUo4TthYTY9", 
    "https://t.me/BannersSocity"
]

DEBUG_MODE = True

# Global variables for online status
ACTIVE_CLIENTS = {}  # phone -> client
ONLINE_STATUS = {}   # phone -> online status
KEEP_ALIVE_TASKS = {} # phone -> keep-alive task
USER_ONLINE_TRACK = {} # user_id -> list of online accounts

logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔍 Check Join Status", callback_data="check_status")],
        [InlineKeyboardButton("📞 Contact Owner", callback_data="contact_owner")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = """🎉 Growth Bot!

📢 Join 3 channels:
1️⃣ t.me/+0gWpc_0xwoE0NWRl
2️⃣ https://t.me/+fe2Q3mUo4TthYTY9
3️⃣ t.me/BannersSocity

✅ Check status after joining!"""
    
    await update.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)

async def update_account_last_active(user_id, phone):
    """Update last active timestamp for account"""
    try:
        user_accs = load_user_accounts(user_id)
        for acc in user_accs:
            if acc['phone'] == phone:
                acc['last_active'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                acc['online_status'] = "🟢 Online"
                save_user_accounts(user_id, user_accs)
                break
    except:
        pass

async def keep_account_online(account_data, user_id):
    """Keep account online by periodic activities"""
    phone = account_data['phone']
    username = account_data.get('username', 'Unknown')
    
    print(f"[KEEP-ALIVE] Starting for {phone} (@{username})")
    
    while True:
        try:
            # Reconnect if disconnected
            if phone not in ACTIVE_CLIENTS:
                client = await get_client_for_account(account_data, keep_alive=True, user_id=user_id)
                if client:
                    ACTIVE_CLIENTS[phone] = client
                    ONLINE_STATUS[phone] = True
                    print(f"[KEEP-ALIVE] Reconnected {phone}")
                else:
                    ONLINE_STATUS[phone] = False
                    print(f"[KEEP-ALIVE] Failed to reconnect {phone}")
                    break
            
            client = ACTIVE_CLIENTS[phone]
            
            if not client.is_connected():
                await client.connect()
                print(f"[KEEP-ALIVE] Reconnected {phone}")
            
            # Send periodic activity to stay online
            try:
                # Update online status
                await client(functions.account.UpdateStatusRequest(offline=False))
                
                # Send typing action to saved messages
                await client.send_message('me', '')
                
                # Get account info to verify online status
                me = await client.get_me()
                
                # Mark as online
                ONLINE_STATUS[phone] = True
                
                # Update last active time in accounts.json
                await update_account_last_active(user_id, phone)
                
                print(f"[KEEP-ALIVE] {phone} is online")
                
            except Exception as e:
                print(f"[KEEP-ALIVE] Error for {phone}: {e}")
                ONLINE_STATUS[phone] = False
            
            # Wait before next check (3 minutes)
            await asyncio.sleep(180)
            
        except asyncio.CancelledError:
            print(f"[KEEP-ALIVE] Stopped for {phone}")
            break
        except Exception as e:
            print(f"[KEEP-ALIVE] Critical error for {phone}: {e}")
            ONLINE_STATUS[phone] = False
            await asyncio.sleep(60)
            continue

async def start_keep_alive_for_account(account_data, user_id):
    """Start keep-alive task for an account"""
    phone = account_data['phone']
    
    if phone in KEEP_ALIVE_TASKS:
        # Stop existing task
        try:
            KEEP_ALIVE_TASKS[phone].cancel()
        except:
            pass
    
    # Start new keep-alive task
    task = asyncio.create_task(keep_account_online(account_data, user_id))
    KEEP_ALIVE_TASKS[phone] = task
    
    return task

async def stop_keep_alive_for_account(phone):
    """Stop keep-alive for an account"""
    if phone in KEEP_ALIVE_TASKS:
        try:
            KEEP_ALIVE_TASKS[phone].cancel()
        except:
            pass
        del KEEP_ALIVE_TASKS[phone]
    
    if phone in ACTIVE_CLIENTS:
        client = ACTIVE_CLIENTS[phone]
        try:
            await client.disconnect()
        except:
            pass
        del ACTIVE_CLIENTS[phone]
    
    if phone in ONLINE_STATUS:
        del ONLINE_STATUS[phone]
    
    print(f"[KEEP-ALIVE] Stopped for {phone}")

async def get_client_for_account(account_data, keep_alive=False, user_id=None):
    """Get connected client for an account"""
    try:
        phone = account_data['phone']
        
        # Return existing active client if available
        if phone in ACTIVE_CLIENTS:
            client = ACTIVE_CLIENTS[phone]
            if client.is_connected():
                return client
            else:
                # Reconnect
                await client.connect()
                return client
        
        # Create new client
        session_name = account_data['session']
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        if await client.is_user_authorized():
            ACTIVE_CLIENTS[phone] = client
            ONLINE_STATUS[phone] = True
            
            # Start keep-alive if requested
            if keep_alive and user_id:
                await start_keep_alive_for_account(account_data, user_id)
            
            return client
        else:
            await client.disconnect()
            return None
    except Exception as e:
        print(f"[ERROR] Getting client for {account_data.get('phone', 'unknown')}: {e}")
        return None

async def main_menu(user_id, update, context):
    user_accs = load_user_accounts(user_id)
    acc_count = len(user_accs)
    
    # Check online accounts
    online_count = 0
    for acc in user_accs:
        if ONLINE_STATUS.get(acc['phone'], False):
            online_count += 1
    
    keyboard = [
        [InlineKeyboardButton("🚀 Growth Menu", callback_data="growth")],
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
        [InlineKeyboardButton("📋 My Accounts", callback_data="manage_account")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
        [InlineKeyboardButton("📞 Contact Owner", callback_data="contact_owner")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"🎉 Access Granted!\n\nAccounts: {acc_count}\n🟢 Online: {online_count}\n🔴 Offline: {acc_count - online_count}\nChoose option:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "check_status":
        await query.edit_message_text("🔍 Checking...")
        joined_count = 0
        status_list = []
        
        for i, channel_id in enumerate(CHANNELS):
            try:
                member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    joined_count += 1
                    status_list.append("✅")
                else:
                    status_list.append("❌")
            except:
                status_list.append("❌")
        
        if joined_count == 3:
            await main_menu(user_id, update, context)
        else:
            text = f"Progress: {joined_count}/3\n\n"
            for i in range(3):
                text += f"{status_list[i]} Channel {i+1}\n"
            
            keyboard = []
            for i in range(3):
                if status_list[i] == "❌":
                    keyboard.append([InlineKeyboardButton(f"Join Ch {i+1}", url=CHANNEL_LINKS[i])])
            keyboard.append([InlineKeyboardButton("🔍 Check Again", callback_data="check_status")])
            
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
        return

    elif data == "growth":
        user_accs = load_user_accounts(user_id)
        acc_count = len(user_accs)
        if acc_count == 0:
            keyboard = [[InlineKeyboardButton("➕ Add Account First", callback_data="add_account")]]
            await query.edit_message_text(
                "⚠️ No accounts!\nAdd account first:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        # Check online accounts
        online_count = 0
        for acc in user_accs:
            if ONLINE_STATUS.get(acc['phone'], False):
                online_count += 1
        
        keyboard = [
            [InlineKeyboardButton("📢 Channel Join", callback_data="channel_join")],
            [InlineKeyboardButton("🎙️ VC Join", callback_data="vc_join")],
            [InlineKeyboardButton("❌ Channel Leave", callback_data="channel_leave")],
            [InlineKeyboardButton("🚪 Logout Account", callback_data="logout_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            f"🚀 Growth Menu\n\nAccounts: {acc_count}\n🟢 Online: {online_count}\n🔴 Offline: {acc_count - online_count}\nChoose:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "channel_join":
        user_accs = load_user_accounts(user_id)
        acc_count = len(user_accs)
        online_count = sum(1 for acc in user_accs if ONLINE_STATUS.get(acc['phone'], False))
        
        await query.edit_message_text(
            f"📢 Channel Join\n\n"
            f"Bot will use ALL {acc_count} accounts\n"
            f"🟢 Online: {online_count}\n"
            f"🔴 Offline: {acc_count - online_count}\n\n"
            f"Send channel link:\n"
            f"@channelname\n"
            f"t.me/channel\n"
            f"t.me/+ABC123\n\n"
            f"⚠️ Note: For private channels,\njoin request will be sent."
        )
        context.user_data['waiting_for_channel'] = True

    elif data == "vc_join":
        user_accs = load_user_accounts(user_id)
        acc_count = len(user_accs)
        if acc_count == 0:
            await query.answer("No accounts added!")
            return
        
        online_count = sum(1 for acc in user_accs if ONLINE_STATUS.get(acc['phone'], False))
        
        await query.edit_message_text(
            f"🎙️ VC Join (All Types Supported)\n\n"
            f"Bot will use ALL {acc_count} accounts\n"
            f"🟢 Online: {online_count}\n"
            f"🔴 Offline: {acc_count - online_count}\n\n"
            f"✅ Supported VC Links:\n"
            f"1. t.me/channelname?voicechat\n"
            f"2. t.me/channelname?videochat\n"
            f"3. t.me/c/1234567890?voicechat (Private Groups)\n\n"
            f"⚠️ Make sure VC is ACTIVE before sending!"
        )
        context.user_data['waiting_for_vc'] = True

    elif data == "channel_leave":
        keyboard = [
            [InlineKeyboardButton("✅ YES - Leave All", callback_data="leave_confirm")],
            [InlineKeyboardButton("❌ NO - Cancel", callback_data="growth")]
        ]
        await query.edit_message_text(
            "⚠️ Channel Leave\n\n"
            "Leave ALL channels from ALL your accounts?\n\n"
            "Are you sure?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "leave_confirm":
        await query.edit_message_text("⏳ Leaving all channels...")
        
        user_accs = load_user_accounts(user_id)
        total_left = 0
        for acc in user_accs:
            try:
                client = await get_client_for_account(acc)
                if client:
                    async for dialog in client.iter_dialogs():
                        if dialog.is_channel and not dialog.entity.megagroup:
                            try:
                                await client(LeaveChannelRequest(dialog.entity))
                                total_left += 1
                                await asyncio.sleep(1)
                            except:
                                pass
                    await client.disconnect()
            except:
                pass
        
        await query.edit_message_text(
            f"✅ Leave Complete!\n\n"
            f"Left: {total_left} channels\n"
            f"Accounts processed!"
        )

    elif data == "logout_menu":
        user_accs = load_user_accounts(user_id)
        if not user_accs:
            await query.answer("No accounts added!")
            return
        
        keyboard = []
        for idx, acc in enumerate(user_accs, 1):
            online_status = "🟢" if ONLINE_STATUS.get(acc['phone'], False) else "🔴"
            btn_text = f"{online_status} Logout: {acc.get('username', 'No @')}"
            callback_data = f"logout_{acc['phone']}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="growth")])
        
        await query.edit_message_text(
            f"🚪 Logout Account\n\n"
            f"Select account to logout:\n"
            f"🟢 = Online | 🔴 = Offline",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("logout_"):
        phone_to_logout = data.split("logout_")[1]
        user_accs = load_user_accounts(user_id)
        
        # Stop keep-alive for this account
        await stop_keep_alive_for_account(phone_to_logout)
        
        new_accs = []
        logged_out_acc = None
        for acc in user_accs:
            if acc['phone'] == phone_to_logout:
                logged_out_acc = acc
            else:
                new_accs.append(acc)
        
        save_user_accounts(user_id, new_accs)
        
        if logged_out_acc and 'session' in logged_out_acc:
            session_file = f"{logged_out_acc['session']}.session"
            try:
                if os.path.exists(session_file):
                    os.remove(session_file)
            except:
                pass
        
        await query.edit_message_text(
            f"✅ Account Logged Out!\n\n"
            f"📱 {phone_to_logout}\n"
            f"👤 {logged_out_acc.get('username', 'Unknown')}\n\n"
            f"Remaining accounts: {len(new_accs)}"
        )

    elif data == "add_account":
        await query.edit_message_text(
            "➕ Add Account (Permanent Storage)\n\n"
            "📱 Send phone number with country code:\n"
            "Example: +919876543210\n\n"
            "⚠️ Once added, account stays until YOU logout!\n"
            "✅ Account will remain ONLINE 24/7"
        )
        context.user_data['waiting_for_phone'] = True

    elif data == "manage_account":
        user_accs = load_user_accounts(user_id)
        if not user_accs:
            await query.answer("No accounts added!")
            return
        
        status_text = ""
        for i, acc in enumerate(user_accs, 1):
            phone = acc['phone']
            
            # Check online status
            online_status = ONLINE_STATUS.get(phone, False)
            status_emoji = "🟢" if online_status else "🔴"
            
            # Get last active time
            last_active = acc.get('last_active', 'Never')
            last_used = acc.get('last_used', 'Never')
            
            status_text += f"{i}. {status_emoji} {acc.get('username', 'No @')}\n"
            status_text += f"   📱 {acc.get('phone')}\n"
            status_text += f"   📊 Status: {'🟢 Online' if online_status else '🔴 Offline'}\n"
            status_text += f"   ⏰ Last Active: {last_active}\n"
            status_text += f"   🕐 Last Used: {last_used}\n\n"
        
        online_count = sum(1 for acc in user_accs if ONLINE_STATUS.get(acc['phone'], False))
        
        text = f"📋 My Accounts ({len(user_accs)})\n"
        text += f"🟢 Online: {online_count} | 🔴 Offline: {len(user_accs)-online_count}\n\n"
        text += status_text
        
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh Status", callback_data="manage_account")],
            [InlineKeyboardButton("➕ Add More", callback_data="add_account")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "help":
        await query.edit_message_text(
            "❓ Help\n\n"
            "🔥 Permanent Account System:\n"
            "• Once added, account stays forever\n"
            "• Survives bot restarts\n"
            "• Only removed when YOU logout\n"
            "• Auto-reconnects if session valid\n\n"
            "🟢 Always Online Feature:\n"
            "• Accounts stay online 24/7\n"
            "• Shows real-time online status\n"
            "• Automatically reconnects\n"
            "• Updates last active time\n\n"
            "💰 Buy source code: @hotbanner"
        )

    elif data == "contact_owner":
        await query.message.reply_text("📞 Owner: @hotbanner")

    elif data == "main_menu":
        await main_menu(user_id, update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # PHONE NUMBER - OTP DEBUG MODE
    if context.user_data.get('waiting_for_phone'):
        phone = text
        
        if not phone.startswith('+'):
            await update.message.reply_text(
                "❌ Invalid format!\n"
                "✅ Correct: +919876543210\n"
                "❌ Wrong: 9876543210\n\n"
                "Send with country code like:\n"
                "+91 for India\n"
                "+1 for US/Canada\n"
                "+44 for UK\n"
                "+92 for Pakistan"
            )
            return
        
        if len(phone) < 10:
            await update.message.reply_text("❌ Phone number too short!")
            return
        
        user_accs = load_user_accounts(user_id)
        for acc in user_accs:
            if acc['phone'] == phone:
                await update.message.reply_text(
                    f"❌ This phone already added!\n\n"
                    f"📱 {phone}\n"
                    f"👤 @{acc.get('username', 'Unknown')}\n\n"
                    f"Use '🚪 Logout Account' to remove first."
                )
                context.user_data.pop('waiting_for_phone', None)
                return
        
        await update.message.reply_text(
            f"🔍 Processing phone: {phone}\n"
            f"⏳ Step 1: Connecting to Telegram API..."
        )
        
        try:
            timestamp = int(time.time())
            session_name = f"sessions/sess_{user_id}_{timestamp}"
            
            os.makedirs("sessions", exist_ok=True)
            
            client = TelegramClient(
                session=session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                device_model="Growth Bot 1.0",
                app_version="1.0",
                system_version="Android 10",
                lang_code="en",
                system_lang_code="en-US"
            )
            
            await update.message.reply_text("✅ Step 1: Connected to API\n⏳ Step 2: Sending OTP request...")
            
            await client.connect()
            
            try:
                sent = await asyncio.wait_for(
                    client.send_code_request(phone),
                    timeout=30
                )
                
                await update.message.reply_text(
                    f"✅ OTP Request Sent Successfully!\n\n"
                    f"📱 To: {phone}\n"
                    f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
                    f"📨 OTP Details:\n"
                    f"• Type: {getattr(sent, 'type', 'SMS')}\n"
                    f"• Length: 5 digits\n"
                    f"• Timeout: 5 minutes\n\n"
                    f"🔢 Now send the 5-digit OTP code you received:\n"
                    f"(Check SMS or Telegram app)"
                )
                
                context.user_data.update({
                    'phone': phone,
                    'client': client,
                    'session': session_name,
                    'step': 'code',
                    'phone_code_hash': sent.phone_code_hash,
                    'timestamp': timestamp,
                    'otp_sent_time': time.time()
                })
                
                context.user_data.pop('waiting_for_phone', None)
                
            except asyncio.TimeoutError:
                await update.message.reply_text(
                    "❌ OTP request timeout!\n\n"
                    "Try again in 1 minute."
                )
                await client.disconnect()
                context.user_data.clear()
                return
                
        except PhoneNumberInvalidError:
            await update.message.reply_text(
                "❌ Invalid phone number!\n\n"
                "Check:\n"
                "1. Country code correct?\n"
                "2. Phone number exists?\n"
                "3. Format: +919876543210\n"
                "4. No spaces or special chars"
            )
            context.user_data.clear()
            
        except FloodWaitError as e:
            wait_time = e.seconds
            minutes = wait_time // 60
            seconds = wait_time % 60
            
            if minutes > 0:
                wait_msg = f"{minutes} minutes {seconds} seconds"
            else:
                wait_msg = f"{seconds} seconds"
            
            await update.message.reply_text(
                f"⏳ Flood Wait!\n\n"
                f"Telegram says: Wait {wait_msg}\n\n"
                f"⚠️ Too many OTP requests!\n"
                f"Try again after {wait_msg}."
            )
            context.user_data.clear()
            
        except ApiIdInvalidError:
            await update.message.reply_text(
                "❌ API Configuration Error!\n\n"
                "Contact bot owner: @hotbanner\n"
                "API_ID/API_HASH invalid!"
            )
            context.user_data.clear()
            
        except Exception as e:
            error_msg = str(e).lower()
            print(f"[ERROR] OTP Send Error: {e}")
            
            if "phone code" in error_msg:
                await update.message.reply_text(
                    "❌ OTP Error!\n\n"
                    "Try:\n"
                    "1. Use different phone\n"
                    "2. Wait 5 minutes\n"
                    "3. Check Telegram app"
                )
            elif "timeout" in error_msg:
                await update.message.reply_text(
                    "❌ Connection Timeout!\n\n"
                    "Check internet and try again."
                )
            elif "network" in error_msg:
                await update.message.reply_text(
                    "❌ Network Error!\n"
                    "Check internet connection."
                )
            else:
                await update.message.reply_text(
                    f"❌ OTP Send Failed!\n\n"
                    f"Error: {error_msg[:100]}\n\n"
                    f"Try:\n"
                    "1. Use correct format\n"
                    "2. Different number\n"
                    "3. Contact owner: @hotbanner"
                )
            context.user_data.clear()
        return

    # OTP CODE - IMPROVED HANDLING
    if context.user_data.get('step') == 'code':
        code = text
        
        if not code.isdigit() or len(code) != 5:
            await update.message.reply_text(
                "❌ Invalid OTP format!\n\n"
                "✅ OTP is 5 digits only\n"
                "Example: 12345\n\n"
                "Send correct 5-digit code:"
            )
            return
        
        client = context.user_data.get('client')
        phone = context.user_data.get('phone')
        phone_code_hash = context.user_data.get('phone_code_hash')
        
        if not client or not phone or not phone_code_hash:
            await update.message.reply_text(
                "❌ Session expired!\n\n"
                "Click '➕ Add Account' again\n"
                "to get new OTP."
            )
            context.user_data.clear()
            return
        
        otp_sent_time = context.user_data.get('otp_sent_time', 0)
        current_time = time.time()
        
        if current_time - otp_sent_time > 300:
            await update.message.reply_text(
                "❌ OTP Expired!\n\n"
                "OTP valid for 5 minutes only.\n"
                "Click '➕ Add Account' again\n"
                "to get new OTP."
            )
            await client.disconnect()
            context.user_data.clear()
            return
        
        await update.message.reply_text("⏳ Verifying OTP...")
        
        try:
            user = await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash
            )
            
            me = await client.get_me()
            
            account = {
                'phone': phone,
                'session': context.user_data['session'],
                'username': me.username or 'No username',
                'first_name': getattr(me, 'first_name', ''),
                'last_name': getattr(me, 'last_name', ''),
                'user_id': me.id,
                'has_2fa': False,
                'added_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'last_used': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'last_active': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'online_status': '🟢 Online'
            }
            
            user_accs = load_user_accounts(user_id)
            user_accs.append(account)
            save_user_accounts(user_id, user_accs)
            
            # Store client in active clients and start keep-alive
            ACTIVE_CLIENTS[phone] = client
            ONLINE_STATUS[phone] = True
            await start_keep_alive_for_account(account, user_id)
            
            context.user_data.clear()
            
            # Don't disconnect - keep it connected for always online
            # await client.disconnect()
            
            await update.message.reply_text(
                f"✅ Account Added Successfully!\n\n"
                f"👤 @{account['username']}\n"
                f"📱 {phone}\n"
                f"🆔 ID: {account['user_id']}\n"
                f"📅 Added: {account['added_date']}\n"
                f"📊 Total Accounts: {len(user_accs)}\n"
                f"🟢 Status: ONLINE 24/7\n\n"
                f"💾 Permanent storage activated!\n"
                f"✅ Bot restart se bhi survive karega!\n"
                f"🟢 Account will stay online automatically!"
            )
            
        except PhoneCodeInvalidError:
            await update.message.reply_text(
                "❌ Wrong OTP Code!\n\n"
                "Send CORRECT 5-digit OTP:\n"
                "(Latest OTP only works)"
            )
            
        except PhoneCodeExpiredError:
            await update.message.reply_text(
                "❌ OTP Expired!\n\n"
                "Click '➕ Add Account' again\n"
                "to get new OTP."
            )
            await client.disconnect()
            context.user_data.clear()
            
        except SessionPasswordNeededError:
            context.user_data['step'] = '2fa'
            await update.message.reply_text(
                f"✅ OTP Verified!\n\n"
                f"🔐 2FA Password Required\n\n"
                f"Send your 2FA password:\n"
                f"(Telegram app > Settings > Privacy > 2FA)"
            )
            
        except Exception as e:
            error_msg = str(e).lower()
            print(f"[ERROR] OTP Verify Error: {e}")
            
            if "flood" in error_msg:
                await update.message.reply_text(
                    "⏳ Flood Wait!\n\n"
                    "Too many attempts.\n"
                    "Wait 5 minutes and try again."
                )
            elif "phone code" in error_msg:
                await update.message.reply_text(
                    "❌ OTP Error!\n\n"
                    "Possible issues:\n"
                    "1. Wrong code entered\n"
                    "2. Code already used\n"
                    "3. New OTP generated\n\n"
                    "Get fresh OTP by clicking '➕ Add Account'"
                )
            else:
                await update.message.reply_text(
                    f"❌ Verification Failed!\n\n"
                    f"Error: {error_msg[:100]}\n\n"
                    f"Try:\n"
                    "1. Fresh OTP request\n"
                    "2. Different phone\n"
                    "3. Contact owner"
                )
            
            await client.disconnect()
            context.user_data.clear()
        return

    # 2FA PASSWORD
    if context.user_data.get('step') == '2fa':
        password = text
        client = context.user_data.get('client')
        
        if not client:
            await update.message.reply_text("❌ Session expired! Click '➕ Add Account' again")
            context.user_data.clear()
            return
        
        try:
            await client.sign_in(password=password)
            
            me = await client.get_me()
            
            account = {
                'phone': context.user_data['phone'],
                'session': context.user_data['session'],
                'username': me.username or 'No username',
                'first_name': getattr(me, 'first_name', ''),
                'last_name': getattr(me, 'last_name', ''),
                'user_id': me.id,
                'has_2fa': True,
                'added_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'last_used': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'last_active': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'online_status': '🟢 Online'
            }
            
            user_accs = load_user_accounts(user_id)
            user_accs.append(account)
            save_user_accounts(user_id, user_accs)
            
            # Store client in active clients and start keep-alive
            ACTIVE_CLIENTS[account['phone']] = client
            ONLINE_STATUS[account['phone']] = True
            await start_keep_alive_for_account(account, user_id)
            
            context.user_data.clear()
            
            # Don't disconnect - keep it connected
            # await client.disconnect()
            
            await update.message.reply_text(
                f"✅ 2FA Account Added PERMANENTLY!\n\n"
                f"👤 @{account['username']}\n"
                f"📱 {account['phone']}\n"
                f"🔐 2FA: Enabled\n"
                f"📅 Added: {account['added_date']}\n"
                f"📊 Total Accounts: {len(user_accs)}\n"
                f"🟢 Status: ONLINE 24/7\n\n"
                f"💾 Permanent storage activated!\n"
                f"✅ Account will stay online 24/7!"
            )
            
        except PasswordHashInvalidError:
            await update.message.reply_text("❌ Wrong 2FA password! Send correct password:")
        except Exception as e:
            error_msg = str(e)
            await update.message.reply_text(f"❌ Error: {error_msg[:100]}\n\nClick '➕ Add Account' to try again.")
            context.user_data.clear()
        return

    # CHANNEL LINK JOIN
    if context.user_data.get('waiting_for_channel'):
        channel = text
        user_accs = load_user_accounts(user_id)
        total_accs = len(user_accs)
        requests_sent = 0
        already_joined = 0
        failed = 0
        public_joined = 0
        
        if total_accs == 0:
            await update.message.reply_text("❌ No accounts added! Add account first.")
            context.user_data.pop('waiting_for_channel', None)
            return
        
        progress_msg = await update.message.reply_text(f"⏳ Processing {total_accs} accounts...")
        
        for idx, acc in enumerate(user_accs, 1):
            try:
                client = await get_client_for_account(acc)
                
                if client:
                    try:
                        entity = await client.get_entity(channel)
                        
                        try:
                            participants = await client.get_participants(entity, limit=1)
                            already_joined += 1
                        except:
                            try:
                                await client(JoinChannelRequest(entity))
                                public_joined += 1
                            except Exception as join_err:
                                error_msg = str(join_err).lower()
                                if any(word in error_msg for word in ["private", "request", "invite", "channelprivate"]):
                                    requests_sent += 1
                                else:
                                    failed += 1
                    except Exception as e:
                        if "t.me/+" in channel:
                            try:
                                hash_part = channel.split("t.me/+")[1]
                                await client(ImportChatInviteRequest(hash_part))
                                public_joined += 1
                            except Exception as invite_err:
                                error_msg = str(invite_err).lower()
                                if any(word in error_msg for word in ["request", "invite"]):
                                    requests_sent += 1
                                else:
                                    failed += 1
                        else:
                            failed += 1
                    
                    await client.disconnect()
                else:
                    failed += 1
                
                await asyncio.sleep(1.5)
                
                if idx % 5 == 0 or idx == total_accs:
                    await progress_msg.edit_text(
                        f"⏳ Processing... {idx}/{total_accs}\n"
                        f"✅ Public Joined: {public_joined}\n"
                        f"📨 Requests Sent: {requests_sent}\n"
                        f"⚠️ Already Joined: {already_joined}\n"
                        f"❌ Failed: {failed}"
                    )
                
            except Exception as e:
                failed += 1
                await asyncio.sleep(1)
                continue
        
        user_accs = load_user_accounts(user_id)
        for acc in user_accs:
            acc['last_used'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            acc['last_active'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_user_accounts(user_id, user_accs)
        
        result_text = f"✅ Channel Join Complete!\n\n"
        result_text += f"📢 Channel: {channel}\n"
        result_text += f"👥 Total Accounts: {total_accs}\n\n"
        result_text += f"📊 Results:\n"
        result_text += f"✅ Public Joined: {public_joined}\n"
        result_text += f"📨 Requests Sent: {requests_sent}\n"
        result_text += f"⚠️ Already Joined: {already_joined}\n"
        result_text += f"❌ Failed: {failed}\n\n"
        
        if requests_sent > 0:
            result_text += f"ℹ️ {requests_sent} accounts sent join requests.\n"
            result_text += f"Wait for admin approval!"
        
        await progress_msg.edit_text(result_text)
        context.user_data.pop('waiting_for_channel', None)
        return

    # ✅✅✅ VC LINK JOIN - SUPPORTS ALL TYPES ✅✅✅
    if context.user_data.get('waiting_for_vc'):
        vc_link = text.strip()
        user_accs = load_user_accounts(user_id)
        total_accs = len(user_accs)
        
        if total_accs == 0:
            await update.message.reply_text("❌ No accounts added! Add account first.")
            context.user_data.pop('waiting_for_vc', None)
            return
        
        # Start processing
        progress_msg = await update.message.reply_text(
            f"🎙️ VC Join Started!\n"
            f"🔗 Link: {vc_link[:50]}...\n"
            f"👥 Accounts: {total_accs}\n"
            f"⏳ Processing..."
        )
        
        # Results tracking
        channel_joined = 0
        already_in_channel = 0
        channel_failed = 0
        vc_joined = 0
        already_in_vc = 0
        vc_failed = 0
        
        # Check link type
        is_private_group = "t.me/c/" in vc_link
        is_public_channel = ("?voicechat" in vc_link or "?videochat" in vc_link) and "t.me/c/" not in vc_link
        
        if not is_private_group and not is_public_channel:
            await update.message.reply_text(
                "❌ Invalid VC link format!\n\n"
                "✅ Supported formats:\n"
                "1. t.me/channelname?voicechat (Public channels)\n"
                "2. t.me/c/1234567890?voicechat (Private groups)\n\n"
                "🎯 Examples:\n"
                "• https://t.me/FriendsChatsWorld?voicechat\n"
                "• t.me/c/1234567890?voicechat"
            )
            context.user_data.pop('waiting_for_vc', None)
            return
        
        print(f"[VC DEBUG] Link: {vc_link}")
        print(f"[VC DEBUG] Private Group: {is_private_group}")
        print(f"[VC DEBUG] Public Channel: {is_public_channel}")
        
        for idx, acc in enumerate(user_accs, 1):
            try:
                client = await get_client_for_account(acc)
                
                if not client:
                    vc_failed += 1
                    continue
                
                try:
                    if is_public_channel:
                        # PUBLIC CHANNEL VC
                        try:
                            # Extract channel username
                            if "https://t.me/" in vc_link:
                                channel_part = vc_link.split("https://t.me/")[1]
                            else:
                                channel_part = vc_link.split("t.me/")[1]
                            
                            if "?" in channel_part:
                                channel_username = channel_part.split("?")[0]
                            else:
                                channel_username = channel_part
                            
                            channel_username = channel_username.rstrip('/')
                            
                            # Join channel first
                            try:
                                entity = await client.get_entity(f"@{channel_username}")
                            except:
                                try:
                                    entity = await client.get_entity(channel_username)
                                except:
                                    entity = await client.get_entity(f"https://t.me/{channel_username}")
                            
                            # Check if already in channel
                            try:
                                await client.get_participants(entity, limit=1)
                                already_in_channel += 1
                            except:
                                # Join channel
                                try:
                                    await client(JoinChannelRequest(entity))
                                    channel_joined += 1
                                    await asyncio.sleep(2)
                                except Exception as join_err:
                                    error_msg = str(join_err).lower()
                                    if "private" in error_msg or "request" in error_msg:
                                        channel_joined += 1
                                    else:
                                        channel_failed += 1
                                        continue
                            
                            # Get VC info
                            full_chat = await client(GetFullChannelRequest(channel=entity))
                            
                            if hasattr(full_chat.full_chat, 'call') and full_chat.full_chat.call:
                                call = full_chat.full_chat.call
                                join_as = await client.get_input_entity(acc['user_id'])
                                
                                # Join VC
                                try:
                                    await client(JoinGroupCallRequest(
                                        call=call,
                                        muted=True,
                                        video_stopped=True,
                                        join_as=join_as,
                                        params=DataJSON(data='{}')
                                    ))
                                    
                                    vc_joined += 1
                                    print(f"[VC] Account {idx} joined public channel VC")
                                    await asyncio.sleep(5)
                                    
                                    # Leave VC
                                    try:
                                        await client(LeaveGroupCallRequest(
                                            call=call,
                                            source=0
                                        ))
                                    except:
                                        pass
                                        
                                except Exception as vc_error:
                                    error_msg = str(vc_error).lower()
                                    if "already" in error_msg or "participant" in error_msg:
                                        already_in_vc += 1
                                    else:
                                        vc_failed += 1
                                        print(f"[VC] Public VC join error: {vc_error}")
                            else:
                                vc_failed += 1
                                
                        except Exception as e:
                            vc_failed += 1
                            print(f"[VC] Public channel error: {e}")
                    
                    elif is_private_group:
                        # PRIVATE GROUP VC
                        try:
                            # Extract chat ID from link
                            # Format: t.me/c/1234567890?voicechat
                            parts = vc_link.split("t.me/c/")[1]
                            chat_id_str = parts.split("?")[0]
                            
                            # Convert to integer (add -100 for private chats)
                            try:
                                chat_id = int("-100" + chat_id_str)
                            except:
                                chat_id = int(chat_id_str)
                            
                            # Get the chat
                            chat = await client.get_entity(chat_id)
                            
                            # Get full chat info
                            full_chat = await client(GetFullChatRequest(chat_id=chat.id))
                            
                            if hasattr(full_chat.full_chat, 'call') and full_chat.full_chat.call:
                                call = full_chat.full_chat.call
                                join_as = await client.get_input_entity(acc['user_id'])
                                
                                # Join VC
                                try:
                                    await client(JoinGroupCallRequest(
                                        call=call,
                                        muted=True,
                                        video_stopped=True,
                                        join_as=join_as,
                                        params=DataJSON(data='{}')
                                    ))
                                    
                                    vc_joined += 1
                                    print(f"[VC] Account {idx} joined private group VC")
                                    await asyncio.sleep(5)
                                    
                                    # Leave VC
                                    try:
                                        await client(LeaveGroupCallRequest(
                                            call=call,
                                            source=0
                                        ))
                                    except:
                                        pass
                                        
                                except Exception as vc_error:
                                    error_msg = str(vc_error).lower()
                                    if "already" in error_msg or "participant" in error_msg:
                                        already_in_vc += 1
                                    else:
                                        vc_failed += 1
                                        print(f"[VC] Private VC join error: {vc_error}")
                            else:
                                vc_failed += 1
                                
                        except Exception as e:
                            vc_failed += 1
                            print(f"[VC] Private group error: {e}")
                    
                except Exception as e:
                    vc_failed += 1
                    print(f"[VC] General error for account {idx}: {e}")
                
                await client.disconnect()
                
                # Update progress
                if idx % 3 == 0 or idx == total_accs:
                    status_text = f"🎙️ VC Join Progress\n"
                    status_text += f"📈 Progress: {idx}/{total_accs}\n\n"
                    
                    if is_public_channel:
                        status_text += f"📢 Public Channel Mode\n"
                        status_text += f"✅ Channel Joined: {channel_joined}\n"
                        status_text += f"⚠️ Already in Channel: {already_in_channel}\n"
                        status_text += f"❌ Channel Failed: {channel_failed}\n\n"
                    
                    status_text += f"🎙️ VC Join Results:\n"
                    status_text += f"✅ VC Joined: {vc_joined}\n"
                    status_text += f"⚠️ Already in VC: {already_in_vc}\n"
                    status_text += f"❌ VC Failed: {vc_failed}"
                    
                    await progress_msg.edit_text(status_text)
                
                # Delay between accounts
                await asyncio.sleep(2)
                
            except Exception as e:
                vc_failed += 1
                print(f"[VC] Outer error for account {idx}: {e}")
                continue
        
        # Final results
        success_rate = (vc_joined / total_accs) * 100 if total_accs > 0 else 0
        
        result_text = f"✅ VC Join Complete!\n\n"
        result_text += f"🔗 Link: {vc_link}\n"
        result_text += f"👥 Total Accounts: {total_accs}\n"
        result_text += f"📊 Success Rate: {success_rate:.1f}%\n\n"
        
        if is_public_channel:
            result_text += f"📢 Public Channel Results:\n"
            result_text += f"✅ Channel Joined: {channel_joined}\n"
            result_text += f"⚠️ Already Member: {already_in_channel}\n"
            result_text += f"❌ Channel Failed: {channel_failed}\n\n"
        
        result_text += f"🎙️ VC Join Results:\n"
        result_text += f"✅ Joined VC: {vc_joined}\n"
        result_text += f"⚠️ Already in VC: {already_in_vc}\n"
        result_text += f"❌ Failed: {vc_failed}\n\n"
        
        if vc_joined > 0:
            result_text += f"🎉 SUCCESS! {vc_joined} accounts joined VC!\n"
            result_text += f"⏰ Stayed 5 seconds in VC\n"
        else:
            result_text += f"⚠️ No accounts could join VC\n"
            result_text += f"Possible issues:\n"
            if is_private_group:
                result_text += f"• Private group VC\n"
                result_text += f"• Need to be group member\n"
            else:
                result_text += f"• Need to join channel first\n"
            result_text += f"• VC not active\n"
            result_text += f"• Telegram API limits\n"
        
        await progress_msg.edit_text(result_text)
        
        # Update last_used timestamp
        user_accs = load_user_accounts(user_id)
        for acc in user_accs:
            acc['last_used'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            acc['last_active'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_user_accounts(user_id, user_accs)
        
        context.user_data.pop('waiting_for_vc', None)
        return

def load_user_accounts(user_id):
    """Load accounts for specific user"""
    try:
        with open('accounts.json', 'r') as f:
            all_accounts = json.load(f)
            return all_accounts.get(str(user_id), [])
    except:
        return []

def save_user_accounts(user_id, accounts_list):
    """Save accounts for specific user"""
    try:
        all_accounts = {}
        if os.path.exists('accounts.json'):
            with open('accounts.json', 'r') as f:
                all_accounts = json.load(f)
        
        all_accounts[str(user_id)] = accounts_list
        
        with open('accounts.json', 'w') as f:
            json.dump(all_accounts, f, indent=2, default=str)
    except Exception as e:
        print(f"Error saving accounts: {e}")

async def reconnect_all_accounts():
    """Reconnect all saved accounts on bot startup"""
    try:
        if not os.path.exists('accounts.json'):
            return
        
        print("[STARTUP] Reconnecting all saved accounts...")
        
        with open('accounts.json', 'r') as f:
            all_accounts = json.load(f)
        
        total_reconnected = 0
        for user_id_str, accounts in all_accounts.items():
            user_id = int(user_id_str)
            for acc in accounts:
                try:
                    phone = acc['phone']
                    
                    # Only reconnect if session file exists
                    session_file = f"{acc['session']}.session"
                    if os.path.exists(session_file):
                        client = await get_client_for_account(acc, keep_alive=True, user_id=user_id)
                        if client:
                            total_reconnected += 1
                            print(f"[STARTUP] Reconnected: {phone}")
                            # Update online status
                            ONLINE_STATUS[phone] = True
                            await asyncio.sleep(1)
                except Exception as e:
                    print(f"[STARTUP] Error reconnecting {acc.get('phone', 'unknown')}: {e}")
                    continue
        
        print(f"[STARTUP] Total reconnected accounts: {total_reconnected}")
    except Exception as e:
        print(f"[STARTUP] Error: {e}")

def main():
    os.makedirs("sessions", exist_ok=True)
    
    print("=" * 50)
    print("🤖 TELEGRAM GROWTH BOT")
    print("=" * 50)
    print(f"🔧 API ID: {API_ID}")
    print(f"🔑 API Hash: {API_HASH[:10]}...")
    print(f"🤖 Bot Token: {BOT_TOKEN[:15]}...")
    print(f"🐞 Debug Mode: {'ON' if DEBUG_MODE else 'OFF'}")
    print("=" * 50)
    
    print("🔄 Testing Telegram API connection...")
    try:
        test_client = TelegramClient("test_session", API_ID, API_HASH)
        asyncio.run(test_client.connect())
        print("✅ API Connection Successful!")
        asyncio.run(test_client.disconnect())
    except Exception as e:
        print(f"❌ API Connection Failed: {e}")
        print("⚠️ Check API_ID and API_HASH")
    
    try:
        with open('accounts.json', 'r') as f:
            all_accs = json.load(f)
            total_users = len(all_accs)
            total_accounts = sum(len(accs) for accs in all_accs.values())
            print(f"📊 Database: {total_accounts} accounts for {total_users} users")
    except:
        print("📊 Database: No accounts yet")
    
    print("=" * 50)
    print("✅ Bot is starting...")
    print("📱 OTP System: Active")
    print("🎙️ VC Join: All Types Supported")
    print("💾 Permanent Storage: Active")
    print("🟢 Always Online: ENABLED (24/7)")
    print("=" * 50)
    
    # Start reconnection in a separate thread
    import threading
    
    def start_reconnection():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(reconnect_all_accounts())
    
    recon_thread = threading.Thread(target=start_reconnection)
    recon_thread.daemon = True
    recon_thread.start()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_error_handler(error_handler)
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("🤖 Bot is running...")
        print("👉 Use /start to begin")
        print("🟢 All accounts will stay online 24/7")
        print("=" * 50)
        
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Bot Startup Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()