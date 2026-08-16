
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Chatbot Operator Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #333; }
        .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .queue-item { border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 5px; }
        .btn { padding: 8px 16px; margin: 5px; border: none; border-radius: 4px; cursor: pointer; }
        .btn-approve { background: #4CAF50; color: white; }
        .btn-edit { background: #2196F3; color: white; }
        .btn-reject { background: #f44336; color: white; }
        .status-badge { padding: 3px 8px; border-radius: 12px; font-size: 12px; }
        .status-pending { background: #fff3cd; color: #856404; }
        .status-approved { background: #d4edda; color: #155724; }
        .tab { display: none; }
        .tab-active { display: block; }
        .tabs { display: flex; margin-bottom: 20px; }
        .tab-btn { padding: 10px 20px; background: #eee; border: none; cursor: pointer; }
        .tab-btn-active { background: white; border-bottom: 2px solid #2196F3; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Chatbot Operator Dashboard</h1>
        <div class="tabs">
            <button class="tab-btn tab-btn-active" onclick="showTab('queue')">Pending Queue</button>
            <button class="tab-btn" onclick="showTab('recent')">Recent Messages</button>
            <button class="tab-btn" onclick="showTab('stats')">Statistics</button>
        </div>

        <div id="queue" class="tab tab-active">
            <div class="card" id="queue-content">
                <p>Loading queue...</p>
            </div>
        </div>

        <div id="recent" class="tab">
            <div class="card" id="recent-content">
                <p>Loading recent messages...</p>
            </div>
        </div>

        <div id="stats" class="tab">
            <div class="card" id="stats-content">
                <p>Loading stats...</p>
            </div>
        </div>
    </div>

    <script>
        async function fetchJSON(path) {
            const res = await fetch(path);
            return res.json();
        }

        async function loadQueue() {
            try {
                const data = await fetchJSON('/api/queue');
                const html = data.map(item => `
                    <div class="queue-item" id="item-${item.id}">
                        <div>
                            <strong>Fan ID:</strong> ${item.user_id}
                            <span class="status-badge status-pending">PENDING</span>
                            <strong>Score:</strong> ${(item.confidence_score * 100).toFixed(0)}%
                        </div>
                        <div><strong>Flags:</strong> ${item.flags.join(', ') || 'none'}</div>
                        <div><strong>Draft:</strong> ${item.draft_content}</div>
                        <div>
                            <button class="btn btn-approve" onclick="approveItem(${item.id})">Approve</button>
                            <button class="btn btn-edit" onclick="editItem(${item.id})">Edit</button>
                            <button class="btn btn-reject" onclick="rejectItem(${item.id})">Reject</button>
                        </div>
                    </div>
                `).join('');
                document.getElementById('queue-content').innerHTML = html || '<p>No pending items</p>';
            } catch (e) {
                document.getElementById('queue-content').innerHTML = '<p>Error loading queue</p>';
            }
        }

        async function approveItem(id) {
            await fetch(`/api/queue/${id}/approve`, { method: 'POST' });
            loadQueue();
        }

        async function rejectItem(id) {
            await fetch(`/api/queue/${id}/reject`, { method: 'POST' });
            loadQueue();
        }

        function editItem(id) {
            const newContent = prompt('Enter new content:');
            if (newContent) {
                fetch(`/api/queue/${id}/edit`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: newContent })
                }).then(() => loadQueue());
            }
        }

        function showTab(tabId) {
            document.querySelectorAll('.tab').forEach(el => el.classList.remove('tab-active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('tab-btn-active'));
            document.getElementById(tabId).classList.add('tab-active');
            event.target.classList.add('tab-btn-active');
            
            if (tabId === 'queue') loadQueue();
            if (tabId === 'recent') loadRecent();
            if (tabId === 'stats') loadStats();
        }

        async function loadRecent() {
            try {
                const data = await fetchJSON('/api/messages/recent?limit=50');
                const html = data.map(m => `
                    <div class="queue-item">
                        <div><strong>${m.direction === 'inbound' ? 'Fan' : 'Bot'}:</strong> ${m.content}</div>
                        <div style="font-size: 12px; color: #666;">${new Date(m.created_at).toLocaleString()}</div>
                    </div>
                `).join('');
                document.getElementById('recent-content').innerHTML = html || '<p>No recent messages</p>';
            } catch (e) {
                document.getElementById('recent-content').innerHTML = '<p>Error loading messages</p>';
            }
        }

        async function loadStats() {
            try {
                const data = await fetchJSON('/api/stats');
                const html = `
                    <div><strong>Total Users:</strong> ${data.total_users}</div>
                    <div><strong>Total Messages:</strong> ${data.total_messages}</div>
                    <div><strong>Pending Queue:</strong> ${data.pending_queue}</div>
                    <div><strong>Auto-approved:</strong> ${data.auto_approved}</div>
                `;
                document.getElementById('stats-content').innerHTML = html;
            } catch (e) {
                document.getElementById('stats-content').innerHTML = '<p>Error loading stats</p>';
            }
        }

        loadQueue();
    </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
