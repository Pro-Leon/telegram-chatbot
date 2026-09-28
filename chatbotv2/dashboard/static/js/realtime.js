/**
 * RealtimeClient - Shared WebSocket client for real-time events.
 *
 * Usage:
 *   const rt = new RealtimeClient();
 *   rt.on('message.created', (event) => { ... });
 *   rt.on('ai.generation_started', (event) => { ... });
 *   rt.onconnected(() => { ... });
 *   rt.ondisconnected(() => { ... });
 */
(function () {
  'use strict';

  var MAX_RECONNECT_DELAY = 30000;
  var INITIAL_RECONNECT_DELAY = 1000;
  var PING_INTERVAL = 30000;
  var DEDUP_CACHE_SIZE = 200;

  function RealtimeClient(options) {
    options = options || {};
    this._url = options.url || _buildUrl();
    this._handlers = {};
    this._connectedHandlers = [];
    this._disconnectedHandlers = [];
    this._reconnectDelay = INITIAL_RECONNECT_DELAY;
    this._reconnectAttempts = 0;
    this._pingInterval = null;
    this._ws = null;
    this._connected = false;
    this._dedupCache = [];
    this._intentionalClose = false;
    this._pollingActive = true;
    this._connect();
  }

  function _buildUrl() {
    var loc = window.location;
    var protocol = loc.protocol === 'https:' ? 'wss:' : 'ws:';
    return protocol + '//' + loc.host + '/ws';
  }

  RealtimeClient.prototype._connect = function () {
    var self = this;
    try {
      this._ws = new WebSocket(this._url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = function () {
      self._connected = true;
      self._pollingActive = false;
      self._reconnectDelay = INITIAL_RECONNECT_DELAY;
      self._reconnectAttempts = 0;
      self._startPing();
      for (var i = 0; i < self._connectedHandlers.length; i++) {
        try { self._connectedHandlers[i](); } catch (e) { console.error('RT handler error:', e); }
      }
    };

    this._ws.onmessage = function (evt) {
      var parsed;
      try { parsed = JSON.parse(evt.data); } catch (e) { return; }
      if (parsed.event_type === 'pong') return;

      var eventId = parsed.event_id;
      if (eventId) {
        if (self._dedupCache.indexOf(eventId) !== -1) return;
        self._dedupCache.push(eventId);
        if (self._dedupCache.length > DEDUP_CACHE_SIZE) {
          self._dedupCache.shift();
        }
      }

      var handler = self._handlers[parsed.event_type];
      if (handler) {
        try { handler(parsed); } catch (e) { console.error('RT handler error:', e); }
      }
    };

    this._ws.onclose = function (evt) {
      self._connected = false;
      self._stopPing();
      if (evt.code === 4001) {
        self._intentionalClose = true;
        self._pollingActive = true;
        for (var i = 0; i < self._disconnectedHandlers.length; i++) {
          try { self._disconnectedHandlers[i]('auth_failed'); } catch (e) { console.error('RT handler error:', e); }
        }
        return;
      }
      self._pollingActive = true;
      for (var j = 0; j < self._disconnectedHandlers.length; j++) {
        try { self._disconnectedHandlers[j]('disconnected'); } catch (e) { console.error('RT handler error:', e); }
      }
      self._scheduleReconnect();
    };

    this._ws.onerror = function () {
      self._ws.close();
    };

    if (!this._beforeunloadBound) {
      this._beforeunloadBound = true;
      var selfUnload = this;
      window.addEventListener('beforeunload', function () {
        if (selfUnload._ws) {
          selfUnload._intentionalClose = true;
          selfUnload._ws.close();
        }
      });
    }
  };

  RealtimeClient.prototype._scheduleReconnect = function () {
    if (this._intentionalClose) return;
    var jitter = Math.random() * 1000;
    var delay = Math.min(this._reconnectDelay + jitter, MAX_RECONNECT_DELAY);
    this._reconnectAttempts++;
    this._reconnectDelay = Math.min(this._reconnectDelay * 2, MAX_RECONNECT_DELAY);
    var self = this;
    setTimeout(function () { self._connect(); }, delay);
  };

  RealtimeClient.prototype._startPing = function () {
    var self = this;
    this._pingInterval = setInterval(function () {
      if (self._ws && self._ws.readyState === WebSocket.OPEN) {
        self._ws.send('ping');
      }
    }, PING_INTERVAL);
  };

  RealtimeClient.prototype._stopPing = function () {
    if (this._pingInterval) {
      clearInterval(this._pingInterval);
      this._pingInterval = null;
    }
  };

  RealtimeClient.prototype.on = function (eventType, handler) {
    this._handlers[eventType] = handler;
    return this;
  };

  RealtimeClient.prototype.onconnected = function (handler) {
    this._connectedHandlers.push(handler);
    return this;
  };

  RealtimeClient.prototype.ondisconnected = function (handler) {
    this._disconnectedHandlers.push(handler);
    return this;
  };

  RealtimeClient.prototype.isConnected = function () {
    return this._connected;
  };

  RealtimeClient.prototype.isPollingActive = function () {
    return this._pollingActive;
  };

  RealtimeClient.prototype.destroy = function () {
    this._intentionalClose = true;
    this._stopPing();
    if (this._ws) {
      this._ws.close();
    }
  };

  window.RealtimeClient = RealtimeClient;
})();
