# Architectural Review & Scaling Proposal: eSports Predictive Engine v2.0

**Date:** April 18, 2026  
**To:** Co-Founder / CTO  
**Subject:** Transitioning our 68% WR eSports betting model from a local monolithic script to a distributed, Dockerized microservice architecture.

---

## 1. Executive Summary
Over the past phase of development, we have validated our core quantitative and qualitative models for predicting eSports match outcomes (Dota 2, LoL, Valorant, CS2, SC2, R6 Siege). 

Our current "toy" implementation utilizes a Multi-Agent Mixture of Experts (MoE) LLM architecture alongside algorithmic bankroll management. The results are highly promising: **we are maintaining a 68% global win rate utilizing Half-Kelly stakes** against Polymarket lines. 

However, our current technical infrastructure—a synchronous pipeline relying on JSON caches and heuristic entity mapping—is hitting its upper limits. To capture alpha faster and scale our API ingestion without breaking rate limits, we need to transition to a distributed microservice architecture hosted locally via Docker on Fedora. This document outlines the current state, identified bottlenecks, and the proposed v2.0 architecture, concluding with an RFC for your review.

---

## 2. Current State: The "Quant-Mental" MoE Pipeline

Our current edge relies on synthesizing structured historical data with unstructured contextual intelligence.

### 2.1. Ingestion & Data Sources
* **Market Filter:** We poll the Polymarket API every 12 hours to identify live/upcoming matches. This ensures we only burn compute on markets with actual liquidity.
* **Structured Data:** We query the PandaScore API (Free Tier) to fetch team rosters, match metadata, and basic historical results.
* **Unstructured Data:** We utilize an LLM "Search Architect" to generate targeted query strings for the Tavily Search API. This surfaces highly contextual "black swan" data (e.g., roster stand-ins, player illness, facility DDoS attacks, or patch note impacts) that traditional stat models miss.

### 2.2. The 7-Agent Synthesis (Mixture of Experts)
Instead of a single zero-shot prompt, we feed the combined briefing document to a 7-agent MoE. Each agent evaluates the match from a distinct persona:
1.  **The Rules Lawyer:** Focuses on tournament formats, map advantages, and patch notes.
2.  **The Bull:** Argues the case for Team A based strictly on positive indicators.
3.  **The Bear:** Argues against Team A based on vulnerabilities and negative sentiment.
4.  **The Sentiment Analyst:** Parses Tavily web-scraped data for team morale and external stressors.
5.  **The Domain Specialist:** Weighs game-specific metrics (e.g., CS2 entry kill conversions, Dota 2 Glicko-2 ratings).
6.  **The Contrarian:** Specifically looks for reasons the market pricing (Polymarket) is fundamentally wrong.
7.  **The Judge (Synthesizer):** Aggregates the inputs.

### 2.3. Decision Matrix & Execution
* **Dynamic Weighting:** Agent outputs are weighted based on their historical **Brier Scores**. If the Contrarian has been hallucinating lately, its influence on the final probability is mathematically reduced.
* **Execution Logic:** The Judge outputs final probabilities and a confidence interval. We run this against the Polymarket price to calculate Expected Value (+EV). If EV is positive, we execute a trade using **Half-Kelly stakes** to protect against the fat-tailed volatility inherent to eSports.

---

## 3. Identified System Bottlenecks

While the mathematical model is sound, the underlying software architecture is brittle:

1.  **The JSON Cache Trap:** We are currently using flat JSON files to store briefing documents, agent predictions, and match outcomes. This makes backtesting, Brier score updating, and complex SQL queries (e.g., "Win rate when EV > 5% on CS2 matches") incredibly slow and memory-intensive.
2.  **Synchronous Execution:** Running 7 agents sequentially creates massive latency. By the time the pipeline finishes evaluating a match, the Polymarket line may have already adjusted, eliminating our +EV window.
3.  **Entity Resolution Failures:** Polymarket strings (e.g., "Will G2 beat FaZe?") do not inherently map to PandaScore IDs (e.g., "G2 Esports - ID: 123"). Our current heuristic mapping is prone to silent failures.
4.  **Correlated Base-Model Bias:** If all 7 MoE agents run on the same underlying foundational model, our cognitive diversity is an illusion. We risk unanimous hallucinations.
5.  **Polling vs. Event-Driven:** Polling Polymarket every 12 hours means we miss intraday volatility spikes caused by breaking news.

---

## 4. Proposed v2.0 Architecture (Dockerized Microservices)

To solve these issues, I propose migrating the stack to a set of Docker containers deployed on a Fedora host. Fedora's native `moby-engine` will handle the containerization efficiently. 

### 4.1. Core Infrastructure
* **Database:** Migrate from JSON to **PostgreSQL**. We will utilize `JSONB` columns to store the raw briefing documents, and relational tables to track `Agent_Performance` (Brier scores) and `Match_Outcomes` for instant querying.
* **Orchestration:** Implement **Apache Airflow** or **Prefect** to handle task scheduling, retries, and dependency mapping.
* **Message Broker:** Implement **Redis** to queue tasks for the LLM agents asynchronously.

### 4.2. Containerized Microservices
1.  **The Scout (Market Listener):** A WebSocket or high-frequency polling container that monitors Polymarket for new listings and price volatility. If a line moves >5% in 10 minutes, it triggers an immediate out-of-band analysis.
2.  **The Resolver (Entity Mapper):** A dedicated service using `thefuzz` (Levenshtein distance). When an unmatched entity appears, it queries an LLM once for validation ("Is X the same as Y?"), maps the ID, and caches it permanently in Postgres.
3.  **The Historian (Structured ETL):** Pulls from PandaScore and specialized scrapers (via Playwright stealth containers if necessary) for deep stats.
4.  **The Journalist (Unstructured ETL):** The Tavily Search Architect.
5.  **The Syndicate (Async MoE):** A Python `asyncio` container that fires prompts to all 7 agents simultaneously. *Crucial upgrade:* We will implement model-mixing (e.g., Claude 3.5 Sonnet for the Bear, Gemini 1.5 Pro for the Domain Specialist, GPT-4o for Sentiment) to ensure true divergence.
6.  **The Bookie (Execution Gate):** Calculates +EV, checks Brier weights from Postgres, applies Half-Kelly sizing, and outputs the final Buy/Pass signal.

---

## 5. Request for Comments (RFC)

Please review the proposed v2.0 architecture and provide your feedback on the following specific items:

### RFC-1: Database Schema & State Management
* **Question:** Given that we need to store highly unstructured briefing documents alongside tightly structured financial data (prices, stakes, Brier scores), do you agree with the PostgreSQL + `JSONB` approach, or should we decouple this into a dual-database setup (e.g., MongoDB for documents, TimescaleDB/Postgres for time-series pricing)?

### RFC-2: Managing Async LLM Rate Limits
* **Question:** Transitioning the 7-agent MoE to `asyncio` will cause massive concurrency spikes on our API keys. What is our preferred strategy for handling `429 Too Many Requests`? Should we implement an exponential backoff directly in the *Syndicate* container, or route all LLM calls through an internal proxy/queue (like LiteLLM + Redis)?

### RFC-3: Infrastructure & Hosting
* **Question:** I am currently testing this on a local Fedora machine using `docker-compose`. For our production deployment, should we keep this on a bare-metal/dedicated server to avoid cloud egress costs and maintain IP proxy stealth for scrapers, or should we migrate the containers to AWS ECS / GCP Cloud Run?

### RFC-4: Entity Mapping Confidence
* **Question:** For the *Resolver* service, what confidence threshold should we require for automated fuzzy-matching before it defaults to a human-in-the-loop (Slack alert) or an expensive LLM validation call?

### RFC-5: Model Diversity Implementation
* **Question:** I proposed splitting the MoE across Claude, Gemini, and OpenAI. Do we have concerns regarding the latency disparity between these providers when running them concurrently in the *Syndicate* container? 

---
*Please drop your notes inline or let's schedule a sync to finalize the data models.*
