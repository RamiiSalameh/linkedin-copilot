### linkedin_copilot

**Local-first LinkedIn job search and Easy Apply copilot using Browser Use and Ollama.**

This project provides a semi-automated assistant that helps you:

- **Search LinkedIn jobs** with structured filters
- **Extract job details** and maintain a local dataset
- **Score jobs against your resume/profile** using a local Ollama model
- **Assist with Easy Apply flows** while keeping you in control

By default, the copilot runs in **safe review mode**:

- Login, captchas, and final submission remain manual
- Captchas remain manual
- Final application submission remains manual
- The agent is instructed to stop before any final submit and **never auto-submit** unless an explicit configuration flag is enabled (and even then, a terminal confirmation is required)

---

### What this project does

- **Job search assistant**
  - Navigates LinkedIn Jobs via `browser-use` + Playwright.
  - Supports keyword and location filters, with an option for **Easy Apply only**.
  - Stores job results locally in SQLite and lets you export them to CSV.
- **Job detail extractor**
  - Opens individual job listings and extracts structured details and raw text via an LLM prompt.
  - Saves raw extracted data under `data/raw_jobs/`.
- **Resume/job matching**
  - Reads your local profile (`data/profiles/profile.json`) and resume (`data/resumes/sample_resume.txt` by default).
  - Uses a local Ollama model to compute:
    - Match score (0–100)
    - Top reasons it matches
    - Missing requirements
    - Suggested resume emphasis bullets
  - Saves markdown summaries and JSON under `data/exports/`.
- **Application assistant (Easy Apply only, review mode)**
  - Orchestrates Easy Apply flows using `browser-use`.
  - Fills obvious non-sensitive fields where possible.
  - Drafts answers for screening questions using your local profile/resume.
  - **Never auto-submits** unless `ALLOW_FINAL_SUBMIT=true` *and* you confirm in the terminal.
- **Local tracking**
  - Persists jobs and match results in a local SQLite database.
  - Exports to CSV for further analysis or spreadsheets.

---

### Safety and review mode

- **Manual login and captchas**
  - LinkedIn login and captchas are always manual.
  - The agent opens LinkedIn Jobs; you complete login yourself in the real browser window.
- **Final submit guardrails**
  - `ALLOW_FINAL_SUBMIT=false` in `.env` and `config/settings.yaml` by default.
  - When disabled:
    - Final submit / send actions are **blocked** in code via `guard_before_submit`.
    - The agent is also instructed in prompts to **not** click final submit.
  - Even if you enable final submit:
    - The tool prints **“MANUAL REVIEW REQUIRED”** via logs.
    - You must confirm in the terminal before a high-risk action is allowed.
- **Dry run**
  - `DRY_RUN=true` by default in `.env`. The current scaffold does not aggressively use this flag yet, but the design keeps all side-effecting operations centralized so you can easily add further checks around it.

---

### Project structure

- **Root**
  - `README.md`: short workspace-level note.
  - `.env.example`: example environment configuration.
  - `pyproject.toml`: project metadata and dependencies.
  - `requirements.txt`: pinned dependencies for quick installs.
  - `Makefile`: helper commands for setup, tests, and example runs.
- **Config**
  - `config/settings.yaml`: YAML configuration for browser, logging, safety, and data paths.
  - `config/selectors.yaml`: LinkedIn-specific selectors and CSS/XPath patterns that you can tweak as LinkedIn’s UI changes.
- **Data (local only)**
  - `data/profiles/profile.json`: sample user profile schema (you should customize this).
  - `data/resumes/sample_resume.txt`: sample plain-text resume.
  - `data/exports/`: CSV and JSON exports (created at runtime).
  - `data/screenshots/`: screenshots for debugging (created at runtime).
  - `data/raw_jobs/`: raw job detail dumps (created at runtime).
  - `data/logs/`: application logs and session files (created at runtime).
- **Source code**
  - `src/linkedin_copilot/`
    - `__init__.py`, `__main__.py`, `main.py`: package and entrypoints.
    - `cli.py`: Typer-based CLI.
    - `config.py`: environment + YAML config loader.
    - `logging_setup.py`: structured logging with `loguru`.
    - `models.py`: Pydantic models and dataclasses for core entities.
    - `db.py`: SQLite persistence layer.
    - `state.py`: in-memory session/application state scaffolding.
    - `utils.py`: helpers (directories, JSON, prompts).
    - `llm.py`: Ollama integration (job summaries, matching, screening answers, planning).
    - `browser.py`: `browser-use` + Playwright configuration and screenshot helper.
    - `prompts.py`: shared LLM prompts.
    - `linkedin/`: LinkedIn-specific automation logic.
    - `scoring/`: resume/job matching logic.
    - `storage/`: exports to CSV/JSON.

---

### Setup steps

1. **Clone / open in Cursor**
  - Open this project folder in Cursor (`Job Search` workspace).
2. **Create a virtual environment**
  ```bash
   cd "Job Search"
   make venv
  ```
3. **Install dependencies**
  ```bash
   make install
  ```
4. **Install Playwright browser dependencies**
  ```bash
   . .venv/bin/activate
   python -m playwright install
  ```
5. **Create your `.env` file**
  ```bash
   cp .env.example .env
  ```
   Adjust values as needed:
  - `**OLLAMA_BASE_URL**`: usually `http://localhost:11434`.
  - `**OLLAMA_MODEL**`: e.g. `qwen2.5-coder:7b`.
  - `**DEFAULT_RESUME_PATH**`: path to your resume file.
  - `**DEFAULT_PROFILE_PATH**`: path to your customized `profile.json`.
  - `**HEADLESS**`: `false` for visible browser (recommended in review mode).
  - `**ALLOW_FINAL_SUBMIT**`: leave as `false` unless you’re absolutely sure.

---

### How to install and use Ollama locally

1. **Install Ollama**
  - Follow the platform instructions on `https://ollama.com` for macOS, Linux, or Windows.
2. **Pull a model**
  ```bash
   ollama pull qwen2.5-coder:7b
  ```
3. **Verify Ollama is running**
  - Ollama typically runs a local server on `http://localhost:11434`.
  - You can test with:

---

### Running the copilot in review mode

With your virtual environment activated:

- **Search jobs**
  ```bash
  python -m linkedin_copilot search --keywords "python backend" --location "Israel" --easy-apply
  ```
  This will:
  - Start a visible Playwright browser via `browser-use`.
  - Instruct the agent to open LinkedIn Jobs and run the search.
  - Collect job cards into the local SQLite database (no applications submitted).
- **List discovered/shortlisted jobs**
  ```bash
  python -m linkedin_copilot shortlist
  ```
- **Export jobs to CSV**
  ```bash
  python -m linkedin_copilot export
  ```
- **Inspect your active profile**
  ```bash
  python -m linkedin_copilot show_profile
  ```

> **Note:** Easy Apply flows and detailed job scoring are scaffolded but not fully wired into the CLI yet. You can call the functions directly from your own scripts and extend the CLI with `apply` / `batch-apply` commands as needed.

---

### Web demo: seed flow (1–6)

For a guided, UI-based demo that matches the requested flow:

1. **Start the app**
   ```bash
   uvicorn linkedin_copilot.web:app --reload
   ```
   Then open `http://127.0.0.1:8000` in your browser.

2. **Upload a CV**
   - On the home page, use the **“Upload your CV”** card to upload a `.txt` CV.
   - The app stores it under `data/resumes/` and shows a preview.

3. **Review extracted profile**
   - Click the **“Profile”** link in the top navigation.
   - You’ll see:
     - The current `profile.json` rendered as JSON.
     - The latest uploaded CV preview and path.

4. **Generate searches**
   - Go back to the home page.
   - In the **“Generate and run a LinkedIn search”** section, set:
     - `Keywords` (e.g. `python backend`).
     - `Location` (e.g. `Israel`).
     - `Easy Apply only` checkbox as desired.

5. **Run a search**
   - Click **“Run LinkedIn search”**.
   - A Browser Use agent will open a real browser window, navigate to LinkedIn Jobs, and run the search.
   - Login and captchas remain manual; complete them in the browser if needed.

6. **See jobs in the UI**
   - After the search completes, navigate to the **“Jobs”** tab (or go to `/jobs`).
   - You’ll see a table of jobs pulled into the local SQLite DB, including:
     - ID, title, company, location
     - Easy Apply flag
     - Current status
     - Link to open the job in a new tab

---

### Configuring profile and resume paths

- **Profile**
  - Default path: `data/profiles/profile.json`
  - Schema includes:
    - `full_name`, `email`, `phone`, `city`, `country`
    - `linkedin_url`, `github_url`, `portfolio_url`
    - `authorized_to_work_regions`
    - `years_experience_by_skill` (mapping skill → years)
    - `top_skills`
    - `preferred_titles`, `preferred_locations`
    - `salary_preferences`, `work_preferences`
    - `education`, `past_roles`
    - `canned_answers` for work authorization, sponsorship, notice period, etc.
  - Customize this file with your real data before serious use.
- **Resume**
  - Default path: `data/resumes/sample_resume.txt`
  - Replace this with your own plaintext or lightly-marked-up resume.
  - Update `DEFAULT_RESUME_PATH` in `.env` or `config/settings.yaml` if you move it.

---

### Troubleshooting tips

- **Browser does not appear**
  - Ensure `HEADLESS=false` in `.env`.
  - Check that `python -m playwright install` has been run inside the virtual environment.
- **Ollama errors / timeouts**
  - Make sure the Ollama server is running and listening on `OLLAMA_BASE_URL`.
  - Confirm the model name in `.env` matches a model you’ve pulled.
- **LinkedIn layout changes**
  - If selectors break, tweak `config/selectors.yaml`.
  - You can add alternative selectors or broaden them as needed.
- **Database issues**
  - The SQLite DB path is controlled via `DATABASE_PATH` / `settings.env.database_path`.
  - Delete the `.sqlite3` file to start fresh if needed.

---

### Warning: final submit is disabled by default

- **Out of the box:**
  - `ALLOW_FINAL_SUBMIT=false` in `.env`.
  - `allow_final_submit: false` in `config/settings.yaml`.
  - `guard_before_submit` will **always block** high-risk actions and log that they are disabled.
- **If you ever enable auto-submit:**
  - You must flip both configuration values.
  - You will still be prompted in the terminal for confirmation before any such action.
  - You are responsible for any applications sent; keep this in **review mode** for normal use.

