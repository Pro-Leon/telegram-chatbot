/* Analytics utility functions — pure, stateless helpers.

Loaded before the Alpine.js component in analytics.html.
All functions are exported on window.AnalyticsUtils.
*/

window.AnalyticsUtils = {
    formatResponseTime(seconds) {
        if (seconds == null) return 'N/A';
        if (seconds < 60) return Math.round(seconds) + 's';
        var mins = Math.floor(seconds / 60);
        var secs = Math.round(seconds % 60);
        return mins + 'm ' + secs + 's';
    },

    timeAgo(isoString) {
        if (!isoString) return '';
        var now = new Date();
        var then = new Date(isoString);
        var diffMs = now - then;
        var diffMins = Math.floor(diffMs / 60000);
        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return diffMins + 'm ago';
        var diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return diffHours + 'h ago';
        var diffDays = Math.floor(diffHours / 24);
        return diffDays + 'd ago';
    },

    formatDate(isoString) {
        if (!isoString) return '';
        var d = new Date(isoString);
        return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) + ' ' +
               d.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit'});
    },

    computePeriodLabel(data) {
        if (!data || !data.period) return '';
        var s = data.period.start;
        var e = data.period.end;
        if (s === 'all time' && e === 'now') return 'All time';
        return s + ' \u2014 ' + e;
    },

    computeAiPercent(data) {
        var ai = (data && data.handling_breakdown && data.handling_breakdown.ai) || 0;
        var op = (data && data.handling_breakdown && data.handling_breakdown.operator) || 0;
        var total = ai + op;
        return total > 0 ? Math.round(ai / total * 100) : 0;
    },

    triggerDownload(blob, filename) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },
};
