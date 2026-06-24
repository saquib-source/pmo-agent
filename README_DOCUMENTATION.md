# PMO-Swarm Documentation Guide

**Last Updated**: June 24, 2026  
**Status**: ✅ Production Ready

---

## 📚 Complete Documentation Index

### For First-Time Users (Start Here)

| Document | Purpose | Time | Location |
|----------|---------|------|----------|
| **[GETTING_STARTED.md](GETTING_STARTED.md)** | Overview, concepts, 5-step quick start | 7 min | Root directory |
| **[pmo-summary.html](pmo-summary.html)** | Visual architecture guide (open in browser) | 5 min | Root directory |
| **[PMO_SUMMARY.txt](PMO_SUMMARY.txt)** | Executive summary & status report | 3 min | Root directory |

### For Technical Deep Dives

| Document | Purpose | Time | Location |
|----------|---------|------|----------|
| **[ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md)** | 8-layer model, data flow, governance gates | 20 min | agents/pmo-swarm/ |
| **[DEPLOY.md](agents/pmo-swarm/DEPLOY.md)** | Step-by-step deployment, APIs, troubleshooting | 30 min | agents/pmo-swarm/ |
| **[CLAUDE.md](CLAUDE.md)** | Project overview & architecture authority | 10 min | Root directory |

### For Operations & Monitoring

Use these while running PMO-Swarm in production:

| Reference | Purpose |
|-----------|---------|
| **GETTING_STARTED.md** (Monitoring section) | Queries for all 4 data layers |
| **ARCHITECTURE.md** (Quick Reference) | Common monitoring queries |
| **PMO_SUMMARY.txt** (Quick Commands) | Copy-paste commands for common tasks |
| Cloud Logging | Real-time logs with trace IDs |
| BigQuery | Operating Briefs and cycle metrics |

---

## 🎯 How to Navigate

### "I want to understand PMO-Swarm"
1. Read [GETTING_STARTED.md](GETTING_STARTED.md) (concepts section)
2. Open [pmo-summary.html](pmo-summary.html) in your browser
3. Skim [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) for the 8-layer model

**Time**: ~20 minutes

---

### "I want to deploy it"
1. Read [GETTING_STARTED.md](GETTING_STARTED.md) (quick start section)
2. Follow the 5-step deployment checklist
3. Verify in Cloud Logging (health checks section)
4. For issues, see [DEPLOY.md](agents/pmo-swarm/DEPLOY.md) troubleshooting

**Time**: ~30 minutes to deploy + monitor first cycle

---

### "I want the technical details"
1. Start with [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) (full read)
2. Then [DEPLOY.md](agents/pmo-swarm/DEPLOY.md) for operations details
3. Review source code: `agents/pmo-swarm/adk/`
4. Check `agent_spec.yaml` for agent definitions

**Time**: ~1-2 hours for comprehensive understanding

---

### "I'm running PMO-Swarm in production"
1. Keep [PMO_SUMMARY.txt](PMO_SUMMARY.txt) handy for quick commands
2. Use [GETTING_STARTED.md](GETTING_STARTED.md) monitoring queries daily
3. Check [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) monitoring checklist weekly
4. For alerts/issues, see [DEPLOY.md](agents/pmo-swarm/DEPLOY.md) troubleshooting

**Resources**:
- Cloud Logging: `resource.type=cloud_run_job AND resource.labels.job_name=pmo-swarm`
- BigQuery: `isrds_pmo.operating_briefs`, `isrds_pmo.cycle_metrics`
- AlloyDB: `pmo_agent.daily_briefings` (30-day backup)

---

## 📖 Document Details

### GETTING_STARTED.md (Your Entry Point)
**What it covers:**
- What PMO-Swarm is (2 min read)
- Key concepts: multi-agent orchestration, governance gates, data persistence (3 min)
- 5-step deployment checklist with commands (8 min)
- Expected log output with emoji indicators (3 min)
- Health check verification (2 min)
- Monitoring queries for all 4 data layers (4 min)
- Troubleshooting FAQ (5 min)

**Best for**: First-time readers, getting started, quick reference

**Read if**: You're new to PMO-Swarm or need a refresher

---

### pmo-summary.html (Visual Reference)
**What it covers:**
- Interactive architecture overview
- 5 skill agents with authority levels
- 8-layer technical model
- Data persistence across 4 destinations
- Step-by-step deployment with commands
- Logging examples with emoji indicators
- Monitoring queries
- Next steps for production hardening

**Best for**: Visual learners, presentations, quick overviews

**Read if**: You prefer diagrams to text, or need to explain to others

---

### PMO_SUMMARY.txt (Executive Summary)
**What it covers:**
- What PMO-Swarm is and does (1 min)
- Deployment status (✅ completed)
- All documentation created (with links)
- What's running in production (1 min)
- Actual log output from first cycle (2 min)
- How to monitor and query (2 min)
- Next steps (priority ordered) (2 min)
- Key metrics and targets (1 min)
- Troubleshooting (1 min)
- Quick command reference (bookmarkable)

**Best for**: Status checks, decision-makers, operations team

**Print this**: Keep it on your desk or bookmark for production operations

---

### ARCHITECTURE.md (Technical Reference)
**What it covers:**
- System overview with ASCII diagram
- Data flow for one cycle (startup → persistence)
- 8-layer architectural model (detailed)
- Authority gradient explanation
- Governance gates workflow
- 4 data destination details
- Monitoring checklist
- Common queries
- Reference implementations

**Best for**: Architects, engineers, deep dives, troubleshooting

**Read if**: You need to understand how all the pieces fit together

---

### DEPLOY.md (Step-by-Step Guide)
**What it covers:**
- Prerequisites (APIs, credentials)
- Build & test locally
- Push to Artifact Registry
- Deploy to Cloud Run
- Execute and monitor
- Cloud Logging queries
- BigQuery queries
- AlloyDB verification
- Common issues and fixes
- Production hardening checklist

**Best for**: DevOps, SRE, deployment specialists

**Read if**: You're responsible for deploying or maintaining PMO-Swarm

---

### CLAUDE.md (Project Overview)
**What it covers:**
- ISRDS platform overview
- Project goals and architecture rules
- Directory structure
- How to use the platform
- Key constraints and sequencing doctrine
- Skills and commands
- Collaboration notes

**Best for**: Understanding ISRDS context, collaborating with others

**Read if**: You're working on the full ISRDS platform (not just PMO-Swarm)

---

## 🔍 Quick Lookup

**Q: How do I deploy PMO-Swarm?**  
A: [GETTING_STARTED.md](GETTING_STARTED.md) → Quick Start section (5 steps)

**Q: How does the architecture work?**  
A: [pmo-summary.html](pmo-summary.html) (visual) + [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) (deep dive)

**Q: What should I expect to see in the logs?**  
A: [PMO_SUMMARY.txt](PMO_SUMMARY.txt) → Logging Examples section

**Q: How do I monitor in production?**  
A: [GETTING_STARTED.md](GETTING_STARTED.md) → Monitoring & Queries section

**Q: What if something breaks?**  
A: [GETTING_STARTED.md](GETTING_STARTED.md) → Troubleshooting (first level)  
Then [DEPLOY.md](agents/pmo-swarm/DEPLOY.md) → Common Issues (deeper)

**Q: What's the current deployment status?**  
A: [PMO_SUMMARY.txt](PMO_SUMMARY.txt) → Deployment Status section

**Q: What do I do next?**  
A: [PMO_SUMMARY.txt](PMO_SUMMARY.txt) → Next Steps section (priority ordered)

---

## 📊 Documentation Statistics

| Document | Lines | Purpose | Audience |
|----------|-------|---------|----------|
| GETTING_STARTED.md | 350 | Getting started guide | Everyone |
| pmo-summary.html | 1000+ | Visual reference | Visual learners |
| ARCHITECTURE.md | 380 | Technical deep dive | Engineers |
| DEPLOY.md | 400+ | Step-by-step deployment | DevOps |
| PMO_SUMMARY.txt | 340 | Executive summary | Everyone |
| CLAUDE.md | 200+ | Project overview | Team leads |

**Total**: ~2,500+ lines of comprehensive documentation

---

## 🎓 Recommended Reading Order

### For Developers
1. GETTING_STARTED.md (understand what it does)
2. pmo-summary.html (visual overview)
3. ARCHITECTURE.md (how it works)
4. Source code: agents/pmo-swarm/adk/

### For DevOps/SRE
1. GETTING_STARTED.md (overview)
2. DEPLOY.md (deployment)
3. PMO_SUMMARY.txt (quick reference)
4. Cloud Logging & BigQuery (operational monitoring)

### For Product/Decision-Makers
1. PMO_SUMMARY.txt (status and next steps)
2. pmo-summary.html (visual overview)
3. GETTING_STARTED.md (key concepts)
4. ARCHITECTURE.md (if needed for approval decisions)

### For New Team Members
1. CLAUDE.md (project context)
2. GETTING_STARTED.md (what PMO-Swarm does)
3. pmo-summary.html (visual architecture)
4. ARCHITECTURE.md (when they need deep knowledge)

---

## 🔗 Quick Links

**Repository**: [github.com/saquib-source/pmo-agent](https://github.com/saquib-source/pmo-agent)

**GCP Project**: isr-division-systems-488723

**Cloud Run Job**: pmo-swarm (us-central1)

**BigQuery Dataset**: isrds_pmo (operating_briefs, cycle_metrics)

**AlloyDB Instance**: pmo-brief-db (daily_briefings table)

---

## 📝 Document Maintenance

**When to update:**
- After deploying a new version → Update DEPLOY.md
- After changing the architecture → Update ARCHITECTURE.md + pmo-summary.html
- After operational learnings → Update GETTING_STARTED.md troubleshooting
- After status changes → Update PMO_SUMMARY.txt

**Ownership**: Mohd Saquib (saquib@isrdsystems.com)

**Last Review**: June 24, 2026

---

## ✅ Checklist for New Users

- [ ] Read GETTING_STARTED.md (Quick Start section)
- [ ] Run the 5-step deployment
- [ ] Verify health checks in Cloud Logging
- [ ] Query BigQuery for Operating Brief
- [ ] Read ARCHITECTURE.md for deeper understanding
- [ ] Set up Cloud Scheduler for automated runs
- [ ] Wire governance gates to Slack/PagerDuty
- [ ] Set up monitoring alerts

**Estimated time**: 1-2 hours for full setup and understanding

---

**Start here**: [GETTING_STARTED.md](GETTING_STARTED.md)
