# dot-hub

The frontend interface for Dot. Serves the Hub UI and provides API routes for Airtable data.

---

## Files

### app.py
**Job:** Flask server that serves the frontend and handles API requests for jobs, clients, people, and tracker data.  
**Connects with:** Airtable (via airtable.py), serves index.html/app.js to browser

---

### app.js
**Job:** Frontend JavaScript. Handles all UI logic - PIN entry, navigation, job cards, modals, WIP view, Tracker view, and Ask Dot conversations.  
**Connects with:** Hub API (app.py), Traffic API (for Ask Dot chat), Proxy (for Teams posting)

---

### index.html
**Job:** HTML structure for the Hub interface. Contains PIN screen, phone/desktop layouts, modals.  
**Connects with:** app.js, styles.css

---

### styles.css
**Job:** All CSS styling for the Hub interface.  
**Connects with:** index.html

---

### airtable.py
**Job:** Helper functions for Airtable operations - getting projects, clients, creating update records.  
**Connects with:** Airtable API, used by app.py

---

### images/
**Job:** Logos, icons, and brand assets (Dot robot, client logos, etc.)  
**Connects with:** index.html, app.js

---

## Architecture

```
Browser → index.html + app.js + styles.css
              ↓
         Hub API (app.py)
              ↓
         Airtable (via airtable.py)
              
Ask Dot chat → Traffic API (separate service)
```

---

## Key Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/api/jobs/all` | Get all active jobs |
| `/api/job/<number>/update` | Update a job + create Updates record |
| `/api/clients` | List all clients |
| `/api/people/<code>` | Get contacts for a client |
| `/api/tracker/data` | Get tracker spend data |
