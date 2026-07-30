# 🚀 [Tips Hindawi](https://www.tipshindawi.com/) Challenge (June–July) 2026

> 🏆 This repository is my official submission for the [ **Tips Hindawi** ](https://www.tipshindawi.com/) **Challenge (June–July) 2026**.

## 👤 Participant

| Field            | Value                                |
| ---------------- | ------------------------------------ |
| Full Name        | Yousef Mohamed Alsayed Elmasry       |
| Project Name     | CoachAI                              |
| GitHub Username  | YousefElmasry1                       |
| Challenge Batch  | June–July 2026                       |
| Training Program | Large Language Models (LLMs) Program |
| Organization     | [**Edrak for Ai**](https://edrak4ai.com/en) |

---

# 📖 Project Overview

Most to-do apps just record what you *plan* to do. **CoachAI** watches what actually happens — then coaches you on it.

Type your day in plain English — *"study database for 2 hours, gym at 6pm, finish the report"* — and the AI Planner (Google Gemini via LangChain) instantly turns it into structured, categorized, prioritized tasks. A deterministic Scheduler slots them onto a real timeline around your work-day start and your breaks. No magic guessing, no reshuffling behind your back — just a clean, predictable plan.

As your day unfolds, you mark tasks done or failed — and that's where CoachAI stops being a planner and starts being a coach. An Analytics Engine mines your history for productivity score, completion and failure rates, streaks, burnout risk, and recurring habits. A Recommendation Engine then fuses *today's* schedule with *your* historical patterns and asks Gemini for real talk: what you're doing right, what's quietly sabotaging you, and exactly what to change — delivered as a strict, Pydantic-validated JSON report, not a vague pep talk.

Fail a task, and CoachAI doesn't just log it — it asks *why* (Distracted? Tired? Ran out of time?), then feeds that into a fully deterministic Analytics Engine that computes 15+ metrics — completion trends, failure-reason breakdowns, burnout risk, best productivity hour, streaks — with **zero AI and zero randomness** involved. Every number comes with its own confidence level based on how much data actually backs it up. Only *then* does Gemini get involved: not to invent statistics, but to interpret real, pre-computed numbers it's explicitly instructed never to recalculate. The result is coaching that's grounded in your actual data, not a hallucinated guess dressed up as insight.

It's not another checklist. It's a system that learns how *you* work, and tells you the truth about it.

---

# ✨ Features

* **AI Day Planner** – converts a free-form description of your day into structured tasks (title, category, priority, estimated duration, fixed/flexible timing), reusing existing categories or proposing new ones intelligently.
* **Deterministic Scheduler** – assigns start/end times to tasks in order, respecting a user-defined work-day start time and any manually configured breaks, without ever reordering or resizing tasks.
* **AI Coach** – analyzes your plan for today (or any past plan), then layers in your last 30 days of history — completion rate, streaks, recurring failure reasons — to generate strengths, weaknesses, and recommendations that reflect your actual track record, not just a single day.
* **Analytics Dashboard** – a suite of metrics computed from task history, including productivity score, completion/failure rates, planning accuracy, streaks, burnout risk indicators, and category/time-based patterns.
* **History Browser** – view past plans, per-plan completion stats, a completion-rate-over-time chart, and generate on-demand AI coaching reports for any past plan.
* **Authentication / Multi-user support** – Guest mode (instant, isolated session), or full Sign Up / Log In with email and password, so plans, tasks, categories, and analytics never mix between users.
* **Settings** – theme switching (dark/light mode), adjustable analytics lookback window, debug mode, category management, and live backend/system status.
* **Achievement Badges** – schema support for streak-, count-, and rate-based badges.
* **Premium custom UI** – dark/light theming with a custom CSS design system layered on top of Streamlit.

---

# 🛠️ Technologies Used

* **Python** – core language
* **Streamlit** – frontend framework / multipage app
* **SQLite** – persistence layer (7 tables: users, categories, plans, tasks, user_profiles, badges, user_badges)
* **Pydantic** – structured data validation for AI outputs
* **LangChain** (`langchain-core`, `langchain-google-genai`) – LLM orchestration
* **Google Gemini** – AI reasoning for the Planner and Recommendation engines
* **Plotly** – data visualizations / charts
* **python-dotenv** – environment variable management
* **Custom CSS** – premium UI theme

---

# ⚙️ Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd <repo-folder>
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up your environment variables**
   Create a `.env` file in the project root with your Google Gemini API key:
   ```
   GOOGLE_API_KEY=your_key_here
   ```

4. **Database — no setup needed**
   The SQLite database (`coach_ai.db`) is created automatically in the project root the first time you run the app, and `schema.sql` is applied automatically if the database is empty. Just make sure `schema.sql` stays in the same folder as `database.py`.

---

# 🚀 Usage

1. Run the Streamlit app:
   ```bash
   streamlit run app.py
   ```
2. On first visit, either continue as a **Guest** for an instant private session, or **Sign Up** / **Log In** to keep your data across sessions.
3. Go to **AI Coach** → describe your day in plain language (e.g. *"study database for 2 hours, gym at 6pm, finish the report"*) to let the AI Planner generate structured tasks.
4. Go to **Today's Schedule** to review/add tasks, set your work-day start time and breaks, run the Scheduler, and mark tasks Completed/Failed as your day progresses.
5. Check **Analytics** for your productivity metrics over an adjustable time window.
6. Return to **AI Coach** to generate a personalized coaching report for today or any past plan.
7. Browse **History** to see past plans and completion trends.
8. Adjust preferences in **Settings** (theme, analytics window, categories, debug mode).

---

# 📸 Demo

🔗 **Live Demo:** [coachai-2dp3dcsljiyemyo2lamh65.streamlit.app](https://coachai-2dp3dcsljiyemyo2lamh65.streamlit.app/)

*(Add screenshots or a GIF here to showcase the UI.)*

---

# 📈 Results

Most "AI productivity coach" tools let the LLM see raw history and reason freely over it — which is exactly how hallucinated stats and made-up patterns creep in. A comparison against three leading tools in this space shows where CoachAI takes a different path:

* **Reclaim.ai** auto-schedules tasks/habits on your calendar and shows an analytics dashboard, but never asks *why* a task failed, and its AI reschedules automatically rather than producing a written coaching report.
* **Motion** reduces stress via a "happiness algorithm" during scheduling, but has no structured failure tracking and no separate historical coaching analysis.
* **Sunsama** comes closest in spirit — it brands itself a "productivity coach" with a daily shutdown reflection — but that reflection is a free-text journal entry the user writes themselves, not an AI report grounded in classified failure data.

CoachAI's core technical achievement is combining three things none of the above do together:

1. A **closed, structured failure-reason taxonomy** (not free text) captured per task and fed directly into analytics.
2. A **fully deterministic Analytics Engine** (`analytics.py`) computing 15+ metrics — productivity score, completion/failure rates, burnout risk, streaks, best productivity hour, failure-reason breakdowns — with the code's own architecture explicitly enforcing **"no AI, no randomness, no network calls,"** each metric carrying its own confidence score based on sample size.
3. An LLM that is only ever handed **pre-computed, formatted numbers** and explicitly instructed never to recompute statistics itself, producing a **Pydantic-validated structured report** — not a free-form journal or a black-box reschedule.

*(Add any user-testing feedback, demo-day results, or evaluation numbers here if available.)*

---

# 🔮 Future Improvements

* **Evolve from a single app into a full platform** — the multi-user foundation (Guest / Sign Up / Log In, per-user data isolation) is already in place, making this a natural next step rather than a rebuild.
* **Notifications** — no push or email reminders exist yet (e.g. upcoming task alerts, daily coaching report ready, streak-at-risk warnings).
* **Paid subscription tiers** — introduce a free tier alongside premium plans (e.g. longer analytics history, unlimited AI Coach reports, priority Gemini usage), building on the existing user model.
* **Automatic badge-earning engine** — the badges schema and `award_badge()` already exist in `database.py`, but no logic currently evaluates streaks or completion rates to award them automatically.
* **Activate fixed-time task scheduling** — `ScheduledTask` already carries `is_fixed_time` / `fixed_start` fields (currently unused, marked "not yet active" in `scheduler.py`), so the Scheduler could respect a user's real fixed appointments instead of only sequential placement.

---

# 📚 About the Challenge

This project was developed as part of the [**Tips Hindawi**](https://www.tipshindawi.com/) **Challenge (June–July) 2026**.

[Tips Hindawi](https://www.tipshindawi.com/) is the internships department of [**Edrak for Ai**](https://edrak4ai.com/en), and the challenge encourages participants to build real-world projects, apply practical skills, and showcase their work through GitHub.

For more information about the challenge, training programs, and upcoming batches, visit the official [Tips Hindawi](https://www.tipshindawi.com/) website.

---

# 📄 License

This project is shared for educational and portfolio purposes.
