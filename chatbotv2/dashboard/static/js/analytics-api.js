/* Analytics API client functions — HTTP calls for the analytics dashboard.

Loaded after analytics-utils.js, before the Alpine.js component.
All functions are exported on window.AnalyticsAPI.
*/

window.AnalyticsAPI = {
    async fetchDashboard(startDate, endDate) {
        var params = new URLSearchParams();
        if (startDate) params.set('start_date', startDate);
        if (endDate) params.set('end_date', endDate);
        var qs = params.toString();
        var resp = await fetch('/api/analytics/dashboard' + (qs ? '?' + qs : ''));
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.error || 'Failed to load analytics');
        }
        return await resp.json();
    },

    async fetchSegments() {
        var resp = await fetch('/api/segments');
        if (resp.ok) return await resp.json();
        return [];
    },

    async fetchConversations(filters, signal) {
        var params = new URLSearchParams();
        if (filters.startDate) params.set('start_date', filters.startDate);
        if (filters.endDate) params.set('end_date', filters.endDate);
        params.set('page', filters.page);
        params.set('page_size', filters.pageSize);
        params.set('sort_by', filters.sortBy);
        params.set('sort_order', filters.sortOrder);
        if (filters.attention) params.set('attention', filters.attention);
        if (filters.tagFilter) params.set('tag_id', filters.tagFilter);
        if (filters.operatorFilter !== '') params.set('assigned_operator_id', filters.operatorFilter);
        if (filters.segmentFilter) params.set('segment_id', filters.segmentFilter);
        var resp = await fetch('/api/analytics/conversations?' + params.toString(), { signal: signal });
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.error || 'Failed to load conversations');
        }
        return await resp.json();
    },

    async fetchConversationDetail(userId, startDate, endDate, signal) {
        var params = new URLSearchParams();
        if (startDate) params.set('start_date', startDate);
        if (endDate) params.set('end_date', endDate);
        var resp = await fetch('/api/analytics/conversations/' + userId + '?' + params.toString(), { signal: signal });
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.error || 'Failed to load detail');
        }
        return await resp.json();
    },

    async fetchOperators() {
        var resp = await fetch('/api/operators');
        if (resp.ok) return await resp.json();
        return [];
    },

    async fetchNotes(userId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/notes');
        if (resp.ok) {
            var data = await resp.json();
            return data.notes || [];
        }
        return [];
    },

    async createNote(userId, content) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/notes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content: content}),
        });
        return { ok: resp.ok, status: resp.status, data: await resp.json().catch(function () { return {}; }) };
    },

    async updateNote(userId, noteId, content) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/notes/' + noteId, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content: content}),
        });
        return { ok: resp.ok, status: resp.status, data: await resp.json().catch(function () { return {}; }) };
    },

    async deleteNote(userId, noteId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/notes/' + noteId, {
            method: 'DELETE',
        });
        return { ok: resp.ok, status: resp.status };
    },

    async fetchConvTags(userId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/tags');
        if (resp.ok) return await resp.json();
        return [];
    },

    async fetchAllTags() {
        var resp = await fetch('/api/conversation-tags');
        if (resp.ok) return await resp.json();
        return [];
    },

    async createTag(name, description) {
        var resp = await fetch('/api/conversation-tags', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: name, description: description || null }),
        });
        return { ok: resp.ok, data: await resp.json().catch(function () { return {}; }) };
    },

    async deleteTag(tagId) {
        var resp = await fetch('/api/conversation-tags/' + tagId, { method: 'DELETE' });
        return { ok: resp.ok };
    },

    async assignTag(userId, tagId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/tags/' + tagId, {
            method: 'POST',
        });
        return { ok: resp.ok };
    },

    async removeTag(userId, tagId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/tags/' + tagId, {
            method: 'DELETE',
        });
        return { ok: resp.ok };
    },

    async markReviewed(userId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/attention', {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({status: 'reviewed'}),
        });
        return { ok: resp.ok, data: await resp.json().catch(function () { return {}; }) };
    },

    async assignOperator(userId, operatorId) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/assignment', {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({assigned_operator_id: parseInt(operatorId) || 0}),
        });
        return { ok: resp.ok, data: await resp.json().catch(function () { return {}; }) };
    },

    async fetchActivity(userId, page, pageSize) {
        var resp = await fetch('/api/analytics/conversations/' + userId + '/activity?page=' + page + '&page_size=' + pageSize);
        if (resp.ok) return await resp.json();
        return null;
    },

    async search(query, scope, segmentId) {
        var params = new URLSearchParams();
        params.set('q', query);
        params.set('scope', scope);
        if (segmentId) params.set('segment_id', segmentId);
        var resp = await fetch('/api/search?' + params.toString());
        if (resp.ok) return await resp.json();
        return null;
    },

    async bulkAssign(userIds, operatorId, segmentId) {
        var resp = await fetch('/api/analytics/conversations/bulk/assign', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_ids: userIds, assigned_operator_id: parseInt(operatorId), segment_id: segmentId || null }),
        });
        return { ok: resp.ok, data: await resp.json().catch(function () { return {}; }) };
    },

    async bulkTag(userIds, tagId, segmentId) {
        var resp = await fetch('/api/analytics/conversations/bulk/tag', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_ids: userIds, tag_id: parseInt(tagId), segment_id: segmentId || null }),
        });
        return { ok: resp.ok, data: await resp.json().catch(function () { return {}; }) };
    },

    async bulkAttention(userIds, status, segmentId) {
        var resp = await fetch('/api/analytics/conversations/bulk/attention', {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_ids: userIds, status: status, segment_id: segmentId || null }),
        });
        return { ok: resp.ok, data: await resp.json().catch(function () { return {}; }) };
    },

    async exportConversationsCSV(filters) {
        var params = new URLSearchParams();
        if (filters.startDate) params.set('start_date', filters.startDate);
        if (filters.endDate) params.set('end_date', filters.endDate);
        params.set('sort_by', filters.sortBy);
        params.set('sort_order', filters.sortOrder);
        if (filters.attention) params.set('attention', filters.attention);
        if (filters.tagFilter) params.set('tag_id', filters.tagFilter);
        if (filters.operatorFilter) params.set('assigned_operator_id', filters.operatorFilter);
        return await fetch('/api/analytics/conversations/export?' + params.toString());
    },

    async exportActivityCSV(userId) {
        return await fetch('/api/analytics/conversations/' + userId + '/activity/export');
    },

    async exportDetailCSV(userId, startDate, endDate) {
        var params = new URLSearchParams();
        if (startDate) params.set('start_date', startDate);
        if (endDate) params.set('end_date', endDate);
        return await fetch('/api/analytics/conversations/' + userId + '/export?' + params.toString());
    },
};
