# 🤖 PMO Agent — AI-Powered Project Management Office

> **Danielle** is an autonomous PMO Execution Agent that monitors your Jira board, chases stalled tickets, identifies blockers, and generates daily Operating Briefs — all powered by Google's Gemini AI.

---

## 📋 Table of Contents

- [What This Agent Does](#-what-this-agent-does)
- [Prerequisites](#-prerequisites-what-you-need-before-starting)
- [Quick Start (5 Minutes)](#-quick-start-5-minutes)
- [Detailed Setup Guide](#-detailed-setup-guide)
  - [Step 1: Clone the Repository](#step-1-clone-the-repository)
  - [Step 2: Install Python](#step-2-install-python)
  - [Step 3: Create a Virtual Environment](#step-3-create-a-virtual-environment)
  - [Step 4: Install Dependencies](#step-4-install-dependencies)
  - [Step 5: Configure Environment Variables](#step-5-configure-environment-variables)
  - [Step 6: Set Up Google Cloud (GCP)](#step-6-set-up-google-cloud-gcp)
  - [Step 7: Set Up Jira API Token](#step-7-set-up-jira-api-token)
- [Running the Agent](#-running-the-agent)
  - [Option A: ADK Web UI (Interactive Chat)](#option-a-adk-web-ui-interactive-chat)
  - [Option B: Autonomous Daemon (Runs by Itself)](#option-b-autonomous-daemon-runs-by-itself)
- [Using with Claude Code (AI Assistant)](#-using-with-claude-code-ai-assistant)
- [Project Structure](#-project-structure)
- [What the Agent Can Do](#-what-the-agent-can-do)
- [Configuration Reference](#-configuration-reference)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)

---

## 🎯 What This Agent Does

| Feature | Description |
|---------|-------------|
| 📊 **Board Scanning** | Reads all active Jira tickets, their statuses, and owners |
| 🔍 **Stalled Ticket Detection** | Finds tickets with no activity for 24+ hours |
| 💬 **Auto Chase** | Sends AI-written follow-up comments on stalled Jira tickets |
| 📝 **Operating Briefs** | Generates daily executive summary reports |
| 🚨 **Escalation Alerts** | Flags critical tickets stalled 72+ hours for leadership |
| 👥 **RACI Analysis** | Identifies tickets without owners (ownership gaps) |
| 📋 **Trust Ledger** | Logs every decision and action for accountability |

---

## ✅ Prerequisites (What You Need Before Starting)

Before you begin, make sure you have these installed on your computer:

| Tool | Why You Need It | How to Check If Installed |
|------|----------------|--------------------------|
| **Python 3.10+** | Runs the agent code | Open Terminal → type `python3 --version` |
| **pip** | Installs Python packages | Open Terminal → type `pip3 --version` |
| **Git** | Downloads the code | Open Terminal → type `git --version` |
| **Google Cloud Account** | For Gemini AI access | [Sign up here](https://console.cloud.google.com/) |
| **Jira Cloud Account** | For board access | Your team's Atlassian URL |

> **⚠️ Python Version:** This project requires **Python 3.10 or higher**. Python 3.9 and below will NOT work (`google-adk`, `truststore`, and other dependencies require 3.10+). **Tested and confirmed working on Python 3.12.**
>
> **💡 Don't have Python 3.12?** See [Step 2: Install Python](#step-2-install-python) below.

---

## 🚀 Quick Start (5 Minutes)

If you're already familiar with Python and the command line, here's the fast path:

```bash
# 1. Clone the repo
git clone git@github.com:saquib-source/pmo-agent.git
cd pmo-agent

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r agents/pmo-execution-agent/adk/requirements.txt

# 4. Set up your environment file
cp agents/pmo-execution-agent/adk/.env.template agents/pmo-execution-agent/adk/.env
# Edit the .env file with your real credentials (see Configuration section below)

# 5. Run the agent (interactive web chat)
cd agents/pmo-execution-agent/adk
adk web .
```

Then open **http://localhost:8000** in your browser. That's it! 🎉

---

## 📖 Detailed Setup Guide

### Step 1: Clone the Repository

Open your **Terminal** app (macOS) or **Command Prompt** (Windows) and run:

```bash
git clone git@github.com:saquib-source/pmo-agent.git
```

Then move into the project folder:

```bash
cd pmo-agent
```

> **📌 What is "cloning"?** It means downloading a copy of the project code from GitHub to your computer.

---

### Step 2: Install Python

#### macOS

**Option A — Homebrew (Recommended):**

If you have Homebrew installed:
```bash
brew install python@3.12
```

**Option B — Official Installer:**

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest Python 3.12+ installer for macOS
3. Double-click the `.pkg` file and follow the wizard
4. Restart your Terminal

#### Windows

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest Python 3.12+ installer
3. **⚠️ IMPORTANT:** Check the box **"Add Python to PATH"** during installation
4. Click "Install Now"
5. Restart your Command Prompt

#### Verify Installation

```bash
python3 --version
# Should print: Python 3.12.x (or higher)
```

---

### Step 3: Create a Virtual Environment

A **virtual environment** is like a clean, isolated workspace for this project's packages. It prevents conflicts with other Python projects on your computer.

**Navigate to the project root (if not already there):**
```bash
cd pmo-agent
```

**Create the virtual environment (use Python 3.12 if available):**
```bash
# Recommended — use Python 3.12 explicitly:
python3.12 -m venv .venv

# Or if python3.12 is your default python3:
python3 -m venv .venv
```

> **⚠️ Important:** If `python3 --version` shows 3.9 or lower, you MUST use `python3.12` (or `python3.11`, `python3.10`) explicitly. A venv created with Python 3.9 will fail to install dependencies.

> This creates a hidden folder called `.venv` inside your project.

**Activate the virtual environment:**

```bash
# macOS / Linux:
source .venv/bin/activate

# Windows (Command Prompt):
.venv\Scripts\activate

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
```

**✅ How to confirm it's active:**

You should see `(.venv)` at the beginning of your terminal prompt, like this:

```
(.venv) yourname@computer pmo-agent %
```

> **⚠️ You must activate the virtual environment EVERY TIME you open a new terminal window to work on this project.** If you don't see `(.venv)` in your prompt, run the `source .venv/bin/activate` command again.

**To deactivate (when you're done):**
```bash
deactivate
```

---

### Step 4: Install Dependencies

With your virtual environment activated (you should see `(.venv)` in your prompt), run:

```bash
pip install -r agents/pmo-execution-agent/adk/requirements.txt
```

This installs the following packages:

| Package | Purpose |
|---------|---------|
| `google-adk` | Google's Agent Development Kit — the agent framework |
| `google-cloud-aiplatform` | Connects to Gemini AI on Google Cloud |
| `pyyaml` | Reads configuration files |
| `httpx` | Makes HTTP requests to Jira API |
| `truststore` | Fixes SSL certificate issues |
| `python-multipart` | Handles file uploads |

**✅ Verify installation:**
```bash
pip list | grep google-adk
# Should show: google-adk    2.x.x
```

> **💡 If you get permission errors**, make sure your virtual environment is activated (check for `(.venv)` in your prompt).

---

### Step 5: Configure Environment Variables

The agent needs credentials and settings stored in a `.env` file. This file contains **secrets** and must **never** be committed to Git.

**Copy the template:**
```bash
cp agents/pmo-execution-agent/adk/.env.template agents/pmo-execution-agent/adk/.env
```

**Open the `.env` file in a text editor and fill in your values:**

```bash
# macOS:
open agents/pmo-execution-agent/adk/.env

# Or use any text editor:
nano agents/pmo-execution-agent/adk/.env        # Terminal editor
code agents/pmo-execution-agent/adk/.env         # VS Code
```

See the **[Configuration Reference](#-configuration-reference)** section below for what each variable means.

---

### Step 6: Set Up Google Cloud (GCP)

The agent uses **Gemini AI** through Google Cloud's Vertex AI. Here's how to set it up:

#### 6a. Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Click **Select a project** → **New Project**
3. Name it something like `pmo-agent`
4. Note your **Project ID** (e.g., `pmo-agent-123456`)

#### 6b. Enable the Vertex AI API

1. In Google Cloud Console, go to **APIs & Services** → **Library**
2. Search for **"Vertex AI API"**
3. Click **Enable**

#### 6c. Create a Service Account Key

1. Go to **IAM & Admin** → **Service Accounts**
2. Click **+ Create Service Account**
3. Name it `pmo-agent-sa` → click **Create**
4. Give it the role: **Vertex AI User** → click **Continue** → **Done**
5. Click on your new service account → **Keys** tab → **Add Key** → **Create new key** → **JSON**
6. A `.json` file will download. **Save it in the project root** (it's gitignored — won't be committed).
7. In your `.env` file, set the path **relative to the `adk/` directory**:
   ```
   # Path goes: adk/ → pmo-execution-agent/ → agents/ → project root
   GOOGLE_APPLICATION_CREDENTIALS=../../../your-downloaded-key.json
   GOOGLE_CLOUD_PROJECT=your-project-id
   ```

> **💡 Relative paths work!** The code auto-resolves them to absolute paths at runtime, so `.env` files are portable across any machine.

> **⚠️ Never share or commit your service account JSON file. It's like a password.**

---

### Step 7: Set Up Jira API Token

The agent reads and writes to your Jira board. You need an API token:

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Label it `PMO Agent` → click **Create**
4. **Copy the token** (you can only see it once!)
5. In your `.env` file, set:
   ```
   JIRA_URL=https://your-org.atlassian.net
   JIRA_EMAIL=your-email@example.com
   JIRA_API_TOKEN=paste-your-token-here
   JIRA_PROJECT=YOUR_PROJECT_KEY
   ```

> **📌 Finding your project key:** Go to your Jira board. The project key is the prefix on your ticket numbers (e.g., if your tickets look like `PROJ-123`, the key is `PROJ`).

---

## ▶️ Running the Agent

### Option A: ADK Web UI (Interactive Chat)

This launches an interactive web interface where you can chat with the agent.

**Step-by-step:**

```bash
# 1. Make sure you're in the project root
cd pmo-agent

# 2. Activate your virtual environment
source .venv/bin/activate

# 3. Navigate to the ADK directory
cd agents/pmo-execution-agent/adk

# 4. Start the agent
adk web .
```

**What you'll see:**
```
INFO:     Started server process
INFO:     Uvicorn running on http://localhost:8000
```

**Open your browser** and go to: **http://localhost:8000**

You'll see a chat interface. Try saying:
- *"What's the status of the board?"*
- *"Run the Operating Brief"*
- *"Check on ISRDS-1510"*
- *"Who's stalling?"*
- *"Find me all critical bugs"*

**To stop the agent:** Press `Ctrl + C` in your terminal.

---

### Option B: Autonomous Daemon (Runs by Itself)

The daemon runs continuously, scanning your Jira board at regular intervals and automatically chasing stalled tickets.

```bash
# 1. Make sure you're in the project root
cd pmo-agent

# 2. Activate your virtual environment
source .venv/bin/activate

# 3. Navigate to the ADK directory
cd agents/pmo-execution-agent/adk

# 4. Run the daemon
python pmo_daemon.py
```

**Daemon modes:**

| Command | What It Does |
|---------|-------------|
| `python pmo_daemon.py` | Runs forever, scanning every 60 minutes (default) |
| `python pmo_daemon.py --once` | Runs ONE scan cycle and exits |
| `python pmo_daemon.py --brief` | Generates a single Operating Brief and exits |

**To stop the daemon:** Press `Ctrl + C` in your terminal.

---

## 🤖 Using with Claude Code (AI Assistant)

If you're using **Claude Code** (Anthropic's AI coding assistant), Claude can set up and run this entire project for you. Here's how:

### Let Claude Do the Setup

Just tell Claude:

```
Clone the pmo-agent repo and set it up for me. Follow the README instructions.
```

Or give Claude more specific instructions:

```
1. Clone git@github.com:saquib-source/pmo-agent.git
2. Create a Python virtual environment
3. Install the requirements
4. Copy the .env.template to .env
5. Then help me fill in the environment variables
```

### Common Claude Code Commands

| What You Want | Tell Claude |
|---------------|-------------|
| Set up the project | *"Set up the pmo-agent project for me"* |
| Start the agent | *"Activate the venv and run `adk web .` from the ADK directory"* |
| Check if it's working | *"Run the agent in `--once` mode and show me the output"* |
| Fix errors | *"I'm getting [error message]. Help me fix it."* |
| Update dependencies | *"Update the pip packages in the virtual environment"* |

### Tips for Working with Claude Code

1. **Always tell Claude your working directory** — Claude needs to know where the project is
2. **Share error messages** — If something breaks, copy-paste the full error
3. **Claude can edit `.env`** — Just tell it what values to use (but share secrets carefully)
4. **Claude can run the agent** — Ask it to run `adk web .` or `python pmo_daemon.py --once`

---

## 📁 Project Structure

```
pmo-agent/
├── README.md                          ← You are here
├── .gitignore                         ← Files excluded from Git
│
└── agents/
    └── pmo-execution-agent/
        ├── agent-spec.yaml            ← Agent specification & metadata
        ├── governance-rules.yaml      ← Rules for human approval gates
        ├── memory-schema.json         ← Memory structure definition
        ├── prompt.md                  ← Danielle's personality & instructions
        ├── swarm-requirements.md      ← Multi-agent coordination rules
        ├── tool-registry.yaml         ← Available tools registry
        ├── workflow-definition.yaml   ← Workflow automation rules
        │
        └── adk/                       ← 🔑 Runtime code (this is where you run from)
            ├── __init__.py            ← Python package entry point
            ├── agent.py              ← Main agent code (Jira tools + Gemini)
            ├── governance.py          ← Trust Ledger & governance gates
            ├── pmo_daemon.py          ← Autonomous daemon (runs on its own)
            ├── requirements.txt       ← Python dependencies
            ├── .env.template          ← Template for environment variables
            ├── .env                   ← Your secrets (NEVER commit this!)
            ├── trust-ledger.jsonl      ← Audit log of all agent actions
            └── briefs/                ← Generated Operating Brief files
```

---

## 🛠 What the Agent Can Do

| Command | Tool Used | Example Prompt |
|---------|-----------|----------------|
| **Query Jira** | `run_jql` | *"Show me all In Progress tickets"* |
| **Check a ticket** | `get_issue` | *"Check on ISRDS-1510"* |
| **Search tickets** | `search_issues` | *"Find all active tickets"* |
| **Post a comment** | `add_comment` | *"Comment on ISRDS-1499 asking for status"* |
| **Move a ticket** | `transition_issue` | *"Move ISRDS-1510 to Done"* |
| **Find stalled work** | `find_stalled_issues` | *"What's stalled?"* |
| **See recent changes** | `get_changes_since` | *"What changed today?"* |
| **Find a person** | `find_user` | *"Look up Todd's account"* |
| **Team roster** | `get_team_members` | *"Who's on the team?"* |
| **Create approval gate** | `governance_gate` | *"This needs COO approval"* |
| **Log a decision** | `log_decision` | Automatic — logs important PMO calls |
| **Draft a chase** | `draft_followup_ping` | *"Draft a chase for stalled tickets"* |

---

## ⚙️ Configuration Reference

All settings go in `agents/pmo-execution-agent/adk/.env`:

### Required Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to GCP service account JSON key (relative to `adk/` or absolute) | `../../../my-key.json` |
| `GOOGLE_CLOUD_PROJECT` | Your Google Cloud project ID | `pmo-agent-123456` |
| `GOOGLE_CLOUD_LOCATION` | GCP region (usually keep default) | `us-central1` |
| `GOOGLE_GENAI_USE_VERTEXAI` | Must be `TRUE` | `TRUE` |
| `JIRA_URL` | Your Jira Cloud URL | `https://your-org.atlassian.net` |
| `JIRA_EMAIL` | Your Jira account email | `you@example.com` |
| `JIRA_API_TOKEN` | Jira API token ([get one here](https://id.atlassian.com/manage-profile/security/api-tokens)) | `ATATT3x...` |
| `JIRA_PROJECT` | Your Jira project key | `ISRDS` |

> **📌 Paths are portable.** Use relative paths (relative to the `adk/` directory) — they are auto-resolved to absolute paths at runtime. This means `.env` works on any machine without editing paths.

### Optional Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENT_MODEL` | Gemini model to use | `gemini-2.5-flash` |
| `TRUST_LEDGER_PATH` | Path to the audit log file | `trust-ledger.jsonl` |
| `PMO_SCAN_INTERVAL_MINUTES` | How often the daemon scans (minutes) | `60` |
| `PMO_STALE_THRESHOLD_HOURS` | Hours before a ticket is "stalled" | `24` |
| `PMO_CHASE_THRESHOLD_HOURS` | Hours before auto-chase comments | `48` |
| `PMO_ESCALATE_THRESHOLD_HOURS` | Hours before escalation alert | `72` |
| `PMO_BRIEF_HOUR` | Hour of day for daily brief (24h) | `7` |
| `PMO_AUTO_COMMENT` | Enable auto-commenting on Jira | `false` |

### SSL Settings (Only if Behind Corporate Firewall)

> **📌 Most users DON'T need this.** SSL settings are only required if you're behind a corporate firewall/proxy that intercepts HTTPS traffic. If you're not sure, skip this section — the agent will use your system's default certificates automatically.
>
> **🛡️ Safe to leave in `.env`:** Even if these variables are set but the cert file is missing (e.g., after a fresh clone), the code **auto-detects** the missing file, prints a warning, and falls back to system defaults. Nothing will crash.

| Variable | Description | Example |
|----------|-------------|--------|
| `SSL_CERT_FILE` | Path to custom CA certificate bundle (relative or absolute) | `./combined-ca-certs.pem` |
| `REQUESTS_CA_BUNDLE` | Same as above (for `requests` library) | `./combined-ca-certs.pem` |
| `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` | Same as above (for gRPC) | `./combined-ca-certs.pem` |

**How to generate the cert bundle (if you need it):**

```bash
# 1. Get your corporate CA certificate from your IT team (e.g., corporate-ca.crt)

# 2. Combine it with Python's default certs:
cd agents/pmo-execution-agent/adk
python3 -c "import certifi; print(certifi.where())"  # Find system certs
cat $(python3 -c "import certifi; print(certifi.where())") corporate-ca.crt > combined-ca-certs.pem

# 3. Uncomment the SSL lines in your .env:
#    SSL_CERT_FILE=./combined-ca-certs.pem
#    REQUESTS_CA_BUNDLE=./combined-ca-certs.pem
#    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=./combined-ca-certs.pem
```

---

## 🔧 Troubleshooting

### "command not found: adk"

The Google ADK CLI isn't installed or not in your PATH.

```bash
# Make sure your venv is active, then:
pip install google-adk
```

### "ModuleNotFoundError: No module named 'google.adk'"

Your virtual environment isn't activated, or dependencies aren't installed.

```bash
# Activate venv:
source .venv/bin/activate

# Reinstall:
pip install -r agents/pmo-execution-agent/adk/requirements.txt
```

### "Jira not configured. Set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env"

Your `.env` file is missing or incomplete.

```bash
# Check it exists:
cat agents/pmo-execution-agent/adk/.env

# If not, create it:
cp agents/pmo-execution-agent/adk/.env.template agents/pmo-execution-agent/adk/.env
# Then edit with your real values
```

### "Authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN."

Your Jira credentials are wrong. Double-check:
1. `JIRA_EMAIL` is your Atlassian account email
2. `JIRA_API_TOKEN` is a valid API token (not your password)
3. Generate a new token at [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens)

### "Cannot connect to Jira" / SSL Errors

If you're behind a corporate firewall or VPN:
1. Ask your IT team for the corporate CA certificate
2. Set the `SSL_CERT_FILE` variable in your `.env`

If you're NOT behind a firewall, make sure SSL variables are **commented out** in `.env`.

### "google.auth.exceptions.DefaultCredentialsError"

Your GCP service account key isn't set up correctly.

```bash
# Verify the file exists (relative to adk/ directory):
ls -la agents/pmo-execution-agent/adk/../../../your-service-account.json

# Make sure it's set in .env (use relative path from adk/ directory):
GOOGLE_APPLICATION_CREDENTIALS=../../../your-service-account.json

# Or use an absolute path:
GOOGLE_APPLICATION_CREDENTIALS=/full/path/to/your-service-account.json
```

### Virtual Environment Issues

```bash
# Delete and recreate:
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r agents/pmo-execution-agent/adk/requirements.txt
```

### Port Already in Use (when running `adk web .`)

```bash
# Find what's using port 8000:
lsof -i :8000

# Kill it:
kill -9 <PID>

# Or use a different port:
adk web . --port 8080
```

---

## 🤝 Contributing

1. **Fork** this repository
2. Create a **feature branch**: `git checkout -b feature/my-feature`
3. **Commit** your changes: `git commit -m "Add my feature"`
4. **Push** to the branch: `git push origin feature/my-feature`
5. Open a **Pull Request**

> **⚠️ Never commit:** `.env` files, service account JSON keys, API tokens, or the `combined-ca-certs.pem` file.

---

## 📜 License

Internal — ISRDS Systems.

---

<p align="center">
  <b>Built with ❤️ by the ISRDS team</b><br>
  Powered by Google ADK + Gemini AI + Jira REST API
</p>
