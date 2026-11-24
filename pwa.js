'use strict';

function offlineStatus(text) {
  document.querySelectorAll('[data-offline-status]').forEach(el => { el.textContent = text; });
}

if ('serviceWorker' in navigator && window.isSecureContext) {
  const service = navigator.serviceWorker;
  const unavailable = 'Mode hors connexion indisponible. Rouvrez l’application avec une connexion.';
  service.register('./sw.js', { scope: './', updateViaCache: 'none' })
    .catch(() => service.getRegistration())
    .then(registration => {
      if (!registration) { offlineStatus(unavailable); return; }
      let lastCheck = 0;
      function showStatus() {
        const check = ++lastCheck;
        const worker = registration.active;
        if (!worker || worker.state !== 'activated') {
          offlineStatus('Préparation du mode hors connexion…');
          return;
        }
        const channel = new MessageChannel();
        const timer = setTimeout(() => finish(false), 12000);
        function finish(ready) {
          clearTimeout(timer);
          channel.port1.close();
          if (check !== lastCheck) return;
          if (!ready) { offlineStatus(unavailable); return; }
          offlineStatus(registration.waiting
            ? 'Prêt hors connexion. Une mise à jour sera appliquée à la prochaine ouverture après fermeture des onglets.'
            : 'Prêt hors connexion.');
        }
        channel.port1.onmessage = event => finish(event.data === true);
        try { worker.postMessage('check-offline', [channel.port2]); }
        catch { finish(false); }
      }
      function watchInstall() {
        const worker = registration.installing;
        if (!worker) return;
        worker.addEventListener('statechange', () => {
          if (worker.state === 'redundant' && !registration.active) offlineStatus(unavailable);
          else showStatus();
        });
      }
      watchInstall();
      showStatus();
      registration.addEventListener('updatefound', watchInstall);
      service.ready.then(showStatus);
      service.addEventListener('controllerchange', showStatus);
      window.addEventListener('online', showStatus);
    })
    .catch(() => offlineStatus(unavailable));
} else {
  offlineStatus('Le mode hors connexion nécessite un navigateur compatible et une adresse HTTPS.');
}
