// ── GRF System — Service Worker ─────────────────────────────────────────────
// Bewusst minimal: dient NUR dazu, die Seite installierbar zu machen (Icon
// auf dem Homescreen, kein Browser-Chrome). Es wird absichtlich NICHTS
// gecacht — alle Inhalte (Live-Ergebnisse, ELO-Stand, Championships) kommen
// live aus Supabase und sollen das auch nach der Installation weiter tun.
// Ein Offline-Cache würde hier veraltete Rally-Ergebnisse zeigen, was mehr
// schadet als hilft. Jede Anfrage geht daher unverändert ans Netzwerk durch.

self.addEventListener('install', (event) => {
  // Sofort aktiv werden, nicht auf das Schließen aller Tabs warten.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Reiner Pass-through — kein caches.match(), kein Response-Caching.
// Der Handler muss trotzdem existieren, damit manche Browser die Seite
// überhaupt als installierbar (PWA) erkennen.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
