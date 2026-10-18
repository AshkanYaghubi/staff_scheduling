#!/usr/bin/env python3
"""
Gemini CLI with File Support & Session Memory
Bypasses web interface lag completely
"""

import google.generativeai as genai
import os
import sys
import json
import readline
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import mimetypes

# Optional: for better file type detection
try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False

from colorama import init, Fore, Style
init(autoreset=True)

# ========== CONFIGURATION ==========
CONFIG_FILE = Path.home() / ".gemini_cli_config.json"
SESSION_FILE = Path.home() / ".gemini_cli_session.json"
HISTORY_FILE = Path.home() / ".gemini_cli_history"

# ========== UTILITY FUNCTIONS ==========
def load_config():
    """Load or create config file"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        config = {
            "api_key": input(f"{Fore.YELLOW}Enter your Gemini API Key: {Style.RESET_ALL}").strip(),
            "model": "gemini-3.1-pro-preview",  # or gemini-2.0-flash-exp
            "temperature": 0.7,
            "max_tokens": 8192
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)  # Secure the file
        return config

def detect_file_type(filepath: str) -> str:
    """Detect MIME type of a file"""
    if HAS_MAGIC:
        return magic.from_file(filepath, mime=True)
    else:
        mime, _ = mimetypes.guess_type(filepath)
        return mime or "text/plain"

def read_file_content(filepath: str) -> str:
    """Read file content with proper encoding handling"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        # Try binary for images, but Gemini needs proper handling
        return f"[Binary file: {os.path.basename(filepath)}]"

# ========== GEMINI SESSION MANAGER ==========
class GeminiSession:
    def __init__(self, config: Dict[str, Any]):
        genai.configure(api_key=config["api_key"])

        self.model = genai.GenerativeModel(
            config["model"],
            generation_config={
                "temperature": config["temperature"],
                "max_output_tokens": config["max_tokens"],
            }
        )
        self.chat = self.model.start_chat(history=[])
        self.config = config
        self.conversation_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Load previous session if exists
        self.load_session()

    def add_files(self, file_paths: List[str]) -> str:
        """Load multiple files into a single prompt"""
        combined = ""
        for path in file_paths:
            if os.path.exists(path):
                content = read_file_content(path)
                combined += f"\n\n--- FILE: {os.path.basename(path)} ---\n{content}\n--- END FILE ---\n"
            else:
                combined += f"\n\n[ERROR: File not found - {path}]\n"
        return combined

    def send_message(self, user_input: str, files: List[str] = None) -> str:
        """Send message with optional files attached"""
        message = user_input

        if files:
            file_content = self.add_files(files)
            message = f"{file_content}\n\n{user_input}"

        try:
            response = self.chat.send_message(message)
            return response.text
        except Exception as e:
            return f"{Fore.RED}Error: {str(e)}{Style.RESET_ALL}"

    def save_session(self):
        """Save conversation history to file"""
        session_data = {
            "conversation_id": self.conversation_id,
            "timestamp": datetime.now().isoformat(),
            "model": self.config["model"],
            "history": [
                {"role": msg.role, "parts": msg.parts[0].text if msg.parts else ""}
                for msg in self.chat.history
            ]
        }
        with open(SESSION_FILE, 'w') as f:
            json.dump(session_data, f, indent=2)
        print(f"{Fore.GREEN}✓ Session saved to {SESSION_FILE}{Style.RESET_ALL}")

    def load_session(self):
        """Load previous conversation history"""
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, 'r') as f:
                    session_data = json.load(f)

                # Restore chat history
                for msg in session_data["history"]:
                    if msg["role"] == "user":
                        self.chat.send_message(msg["parts"])
                    # Note: Responses are auto-stored by the chat session

                self.conversation_id = session_data["conversation_id"]
                print(f"{Fore.CYAN}✓ Loaded previous session ({len(session_data['history'])//2} messages){Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.YELLOW}⚠ Could not load session: {e}{Style.RESET_ALL}")

    def clear_session(self):
        """Start a fresh conversation"""
        self.chat = self.model.start_chat(history=[])
        self.conversation_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
        print(f"{Fore.GREEN}✓ Session cleared{Style.RESET_ALL}")

    def show_context(self):
        """Show current conversation context size"""
        msg_count = len(self.chat.history)
        if msg_count == 0:
            print(f"{Fore.YELLOW}No messages in current session{Style.RESET_ALL}")
        else:
            print(f"{Fore.CYAN}Current session: {msg_count // 2} exchanges ({msg_count} total messages){Style.RESET_ALL}")
            print(f"{Fore.CYAN}Conversation ID: {self.conversation_id}{Style.RESET_ALL}")

    def export_conversation(self, filename: str = None):
        """Export conversation to markdown file"""
        if not filename:
            filename = f"gemini_conversation_{self.conversation_id}.md"

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"# Gemini Conversation\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Model:** {self.config['model']}\n\n")
            f.write("---\n\n")

            for msg in self.chat.history:
                role = "**User:**" if msg.role == "user" else "**Gemini:**"
                f.write(f"{role}\n\n")
                f.write(f"{msg.parts[0].text}\n\n")
                f.write("---\n\n")

        print(f"{Fore.GREEN}✓ Conversation exported to {filename}{Style.RESET_ALL}")

# ========== COMMAND PARSER ==========
def parse_command(user_input: str) -> tuple:
    """Parse slash commands"""
    if not user_input.startswith('/'):
        return "message", user_input, []

    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == '/file':
        return "file", user_input[6:].strip(), args
    elif cmd == '/clear':
        return "clear", None, args
    elif cmd == '/context':
        return "context", None, args
    elif cmd == '/save':
        return "save", None, args
    elif cmd == '/export':
        return "export", None, args
    elif cmd == '/quit' or cmd == '/exit':
        return "quit", None, args
    elif cmd == '/help':
        return "help", None, args
    else:
        return "message", user_input, args

def show_help():
    """Display help menu"""
    print(f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════╗
║                    Gemini CLI Commands                         ║
╠══════════════════════════════════════════════════════════════╣
║  {Fore.GREEN}/file <path1> [path2 ...]{Style.RESET_ALL}              Attach file(s) to next message  ║
║  {Fore.GREEN}/clear{Style.RESET_ALL}                               Start a fresh conversation       ║
║  {Fore.GREEN}/context{Style.RESET_ALL}                             Show current session size        ║
║  {Fore.GREEN}/save{Style.RESET_ALL}                                Save session to file             ║
║  {Fore.GREEN}/export [filename]{Style.RESET_ALL}                    Export to markdown               ║
║  {Fore.GREEN}/quit or /exit{Style.RESET_ALL}                       Exit the program                 ║
║  {Fore.GREEN}/help{Style.RESET_ALL}                                Show this menu                   ║
╚══════════════════════════════════════════════════════════════╝

{Fore.YELLOW}Quick Start Examples:{Style.RESET_ALL}
  • Just type normally: {Fore.GREEN}What is a closure in JavaScript?{Style.RESET_ALL}
  • With files: {Fore.GREEN}/file script.js {Style.RESET_ALL}then{Fore.GREEN} Find bugs in this code{Style.RESET_ALL}
  • Multiple files: {Fore.GREEN}/file main.py utils.py{Style.RESET_ALL} then{Fore.GREEN} Explain this project{Style.RESET_ALL}
    """)

# ========== MAIN LOOP ==========
def main():
    print(f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════════╗
║                    Gemini CLI with File Support                      ║
║                      No more web interface lag!                       ║
╠══════════════════════════════════════════════════════════════════╣
║  Type {Fore.GREEN}/help{Fore.CYAN} for commands, {Fore.GREEN}/quit{Fore.CYAN} to exit                  ║
╚══════════════════════════════════════════════════════════════════╝
{Style.RESET_ALL}""")

    # Load config
    config = load_config()

    # Initialize session
    session = GeminiSession(config)

    # Setup command history
    try:
        readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass

    pending_files = []

    # Main loop
    while True:
        try:
            # Show pending files indicator
            file_indicator = f"{Fore.YELLOW}[{len(pending_files)} files] {Style.RESET_ALL}" if pending_files else ""
            user_input = input(f"{Fore.GREEN}You{file_indicator}> {Style.RESET_ALL}").strip()

            if not user_input:
                continue

            # Add to history
            readline.add_history(user_input)
            readline.write_history_file(HISTORY_FILE)

            # Parse command
            cmd_type, content, args = parse_command(user_input)

            if cmd_type == "quit":
                session.save_session()
                print(f"{Fore.GREEN}Goodbye! Session saved.{Style.RESET_ALL}")
                break

            elif cmd_type == "clear":
                session.clear_session()
                pending_files = []
                continue

            elif cmd_type == "context":
                session.show_context()
                continue

            elif cmd_type == "save":
                session.save_session()
                continue

            elif cmd_type == "export":
                filename = args[0] if args else None
                session.export_conversation(filename)
                continue

            elif cmd_type == "help":
                show_help()
                continue

            elif cmd_type == "file":
                # Collect files for next message
                if content:
                    pending_files = [f.strip() for f in content.split() if f.strip()]
                    print(f"{Fore.GREEN}✓ Added {len(pending_files)} file(s) to next message{Style.RESET_ALL}")
                    for f in pending_files:
                        exists = "✓" if os.path.exists(f) else "✗"
                        print(f"  {Fore.CYAN}{exists}{Style.RESET_ALL} {f}")
                continue

            else:  # Regular message
                # Send message with files
                print(f"{Fore.MAGENTA}Gemini is thinking...{Style.RESET_ALL}")
                response = session.send_message(content, pending_files if pending_files else None)

                print(f"{Fore.BLUE}Gemini:{Style.RESET_ALL}\n{response}\n")

                # Clear pending files after sending
                pending_files = []

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Interrupted. Type /quit to exit.{Style.RESET_ALL}")
            continue
        except EOFError:
            print(f"\n{Fore.GREEN}Goodbye!{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"{Fore.RED}Unexpected error: {e}{Style.RESET_ALL}")

if __name__ == "__main__":
    main()
