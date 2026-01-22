# Dot Universal Schema

One brain. Two inputs. Same Dot.

## Overview

```
Email â†’ PA Listener â†’ /traffic â†’ Workers / Response
                          â†‘
Hub â†’ Ask Dot input â”€â”€â”€â”€â”€â”€â”˜
```

Dot is the brain, not the hands. She reads, routes, and responds - but workers do the actual work.

---

## 1. INPUT (to Dot/Traffic)

```javascript
{
  // === REQUIRED ===
  source: "email" | "hub",
  content: "The actual message/question",
  
  // === SENDER ===
  senderEmail: "michael@hunch.co.nz",
  senderName: "Michael",
  
  // === EMAIL-ONLY ===
  subject: "FW: Please file this",
  recipients: ["dot@hunch.co.nz", "emma@hunch.co.nz"],
  hasAttachments: true,
  attachmentNames: ["LAB 055 - Speech v2.pdf"],
  attachmentList: [...],              // Full attachment data for workers
  internetMessageId: "...",           // For deduplication
  conversationId: "...",              // For reply threading
  receivedDateTime: "2026-01-19T10:30:00Z",
  
  // === HUB-ONLY ===
  sessionId: "Michael",               // For conversation memory
  userId: "michael"                   // Logged-in user ID
}
```

---

## 2. OUTPUT (from Dot/Traffic)

```javascript
{
  // === ALWAYS PRESENT ===
  type: "action" | "answer" | "confirm" | "clarify" | "redirect",
  message: "Natural language response",
  confidence: "high" | "medium" | "low",
  reason: "Brief explanation for logging",
  
  // === CONTEXT (when identified) ===
  clientCode: "LAB" | null,
  clientName: "Labour" | null,
  jobNumber: "LAB 055" | null,
  
  // === FOR TYPE: "action" ===
  route: "file" | "update" | "triage" | "incoming" | "wip" | "todo" | "tracker",
  
  // === FOR TYPE: "answer" ===
  data: { ... },                      // Tool results (spend, people, etc.)
  jobs: [ ... ] | null,               // Job cards if relevant
  nextPrompt: "Want to see the breakdown?" | null,
  
  // === FOR TYPE: "confirm" ===
  originalIntent: "update" | "file",
  jobs: [ ... ],                      // Jobs to choose from
  
  // === FOR TYPE: "clarify" ===
  clarifyType: "no_client" | "no_job" | "no_idea",
  
  // === FOR TYPE: "redirect" ===
  redirectTo: "wip" | "tracker",
  redirectParams: { client: "SKY" },
  url: "/tracker?client=SKY"
}
```

---

## 3. JOB CARD (universal)

This is what goes in the `jobs` array. Must match what `createUniversalCard()` expects.

```javascript
{
  // === IDENTITY ===
  jobNumber: "LAB 055",
  jobName: "Election 26",
  clientCode: "LAB",
  
  // === STATUS ===
  stage: "Clarify" | "Simplify" | "Craft" | "Refine" | "Deliver",
  status: "Incoming" | "In Progress" | "On Hold" | "Completed" | "Archived",
  withClient: true | false,
  
  // === DATES ===
  updateDue: "2026-01-22",           // ISO format (YYYY-MM-DD) for JS Date parsing
  liveDate: "Jan" | "Feb" | "Tbc",   // Month dropdown - display as-is
  lastUpdated: "21 Jan",             // Friendly format from Update History
  
  // === CONTENT ===
  description: "What's this job all about",
  update: "Latest status message",
  updateHistory: ["21 Jan | With client", "18 Jan | First draft done"],
  projectOwner: "Sarah",              // Client contact name
  
  // === LINKS ===
  channelUrl: "https://teams.microsoft.com/..."
}
```

### Airtable Field Mapping

| Schema Field | Airtable Field | Format |
|--------------|----------------|--------|
| `updateDue` | `Update Due` | D/M/YYYY → converted to ISO |
| `liveDate` | `Live` | Month dropdown (Jan, Feb, Tbc) |
| `lastUpdated` | Parsed from `Update History` | "DD Mon" |
| `update` | `Update` | Text |
| `updateHistory` | `Update History` | Array of "DD Mon \| text" |

### Note: `filesUrl`

`filesUrl` is NOT part of the job card schema passed through Traffic. It's an **Airtable field** on the Projects table that stores the SharePoint folder URL for each job.

- dot-file looks it up to know WHERE to file attachments
- Hub may fetch it separately for "Files" links
- If missing, dot-file returns "No job bag" error

---

## 4. DOT'S FIVE MOVES

| Type | When | Dot Says | System Does |
|------|------|----------|-------------|
| `answer` | Easy question | "Sky's spent $6.2K" | Render message + optional cards |
| `action` | Task requested | "On it - filing now" | Route to worker |
| `redirect` | Complex query | "Check Tracker for the full picture" | Navigate (Hub) or send link (Email) |
| `confirm` | Job unclear | "Which Labour job?" | Show job cards to pick |
| `clarify` | Stuck | "Which client?" | Ask for more info |

---

## 5. MEMORY

- **Hub**: Conversation memory per `sessionId` (last 20 messages, 30 min timeout)
- **Email**: Stateless (each email standalone)

---

## 6. RESPONSE EXAMPLES

### TYPE: answer
```json
{
  "type": "answer",
  "message": "Sky's looking healthy - $6.2K spent, $3.8K to play with.",
  "confidence": "high",
  "clientCode": "SKY",
  "clientName": "Sky TV",
  "jobNumber": null,
  "jobs": null,
  "nextPrompt": "Want to see what's in progress?",
  "reason": "Simple spend lookup"
}
```

### TYPE: answer with jobs
```json
{
  "type": "answer",
  "message": "Three things due this week:",
  "confidence": "high",
  "clientCode": null,
  "clientName": null,
  "jobNumber": null,
  "jobs": [
    {"jobNumber": "LAB 055", "jobName": "Election 26", "stage": "Craft", "status": "In Progress", "updateDue": "2026-01-22", "withClient": false},
    {"jobNumber": "SKY 042", "jobName": "Summer Campaign", "stage": "Refine", "status": "In Progress", "updateDue": "2026-01-23", "withClient": true}
  ],
  "nextPrompt": "Want me to filter by client?",
  "reason": "Job query - due this week"
}
```

### TYPE: action
```json
{
  "type": "action",
  "route": "file",
  "message": "On it.",
  "confidence": "high",
  "clientCode": "LAB",
  "clientName": "Labour",
  "jobNumber": "LAB 055",
  "reason": "Explicit file request with job number"
}
```

### TYPE: confirm
```json
{
  "type": "confirm",
  "message": "Which Labour job are we talking about?",
  "confidence": "medium",
  "clientCode": "LAB",
  "clientName": "Labour",
  "jobNumber": null,
  "originalIntent": "update",
  "jobs": [
    {"jobNumber": "LAB 055", "jobName": "Election 26", "stage": "Craft", "status": "In Progress", "updateDue": "2026-01-22", "withClient": false},
    {"jobNumber": "LAB 056", "jobName": "Speech Writing", "stage": "Refine", "status": "In Progress", "updateDue": "2026-01-24", "withClient": true}
  ],
  "reason": "Multiple active Labour jobs"
}
```

### TYPE: redirect
```json
{
  "type": "redirect",
  "message": "For the full picture, Tracker's your friend - I've set it to Sky for you.",
  "confidence": "high",
  "clientCode": "SKY",
  "clientName": "Sky TV",
  "jobNumber": null,
  "redirectTo": "tracker",
  "redirectParams": {"client": "SKY"},
  "url": "/tracker?client=SKY",
  "reason": "Complex spend query - better in Tracker"
}
```

### TYPE: clarify
```json
{
  "type": "clarify",
  "message": "Throw me a bone here - which client?",
  "confidence": "low",
  "clientCode": null,
  "clientName": null,
  "jobNumber": null,
  "clarifyType": "no_client",
  "reason": "No client indicators found"
}
```

---

## 7. RENDERING

### Hub
- Uses `createUniversalCard()` in app.js
- Interactive: clickable, expandable, has "Update" button
- Handles all response types with `renderResponse()`

### Email
- Uses `_format_job_cards()` in connect.py
- Static HTML with links to Hub
- Cards become clickable links, not interactive elements

Both render the **same data** - different presentation for different mediums.

```
Dot returns: jobs: [{ jobNumber: "LAB 055", ... }]
                    â†“
        â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
        â†“                     â†“
   Hub renders            Email renders
   interactive card       static HTML card
```

---

## 8. WORKERS

Workers receive a universal payload regardless of source:

```javascript
{
  // Routing
  route: "file",
  confidence: "high",
  
  // Job
  jobNumber: "LAB 055",
  clientCode: "LAB",
  clientName: "Labour",
  
  // Sender
  senderName: "Michael",
  senderEmail: "michael@hunch.co.nz",
  
  // Content
  subjectLine: "Please file this",
  emailContent: "...",
  
  // Attachments
  hasAttachments: true,
  attachmentNames: ["LAB 055 - Speech v2.pdf"],
  
  // Source
  source: "email" | "hub"
}
```

Workers don't care how they were triggered. They do their job and return a result.

---

*Last verified: January 2026*
