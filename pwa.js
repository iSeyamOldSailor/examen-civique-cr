'use strict';

function offlineStatus(text) {
  document.querySelectorAll('[data-offline-status]').forEach(el => { el.textContent = text; });
}

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('./sw.js', { scope: './', updateViaCache: 'none' })
    .then(registration => {
      function showStatus() {
        if (registration.active && registration.active.state === 'activated') {
          offlineStatus(registration.waiting
            ? 'Prêt hors connexion. Une mise à jour sera appliquée à la prochaine ouverture après fermeture des onglets.'
            : 'Prêt hors connexion.');
        } else {
          offlineStatus('Préparation du mode hors connexion…');
        }
      }
      function watchInstall() {
        const worker = registration.installing;
        if (!worker) return;
        worker.addEventListener('statechange', () => {
          if (worker.state === 'redundant' && !registration.active) {
            offlineStatus('Mode hors connexion indisponible. Rouvrez l’application avec une connexion.');
          } else showStatus();
        });
      }
      watchInstall();
      showStatus();
      registration.addEventListener('updatefound', watchInstall);
      navigator.serviceWorker.ready.then(showStatus);
      navigator.serviceWorker.addEventListener('controllerchange', showStatus);
    })
    .catch(() => offlineStatus('Mode hors connexion indisponible. Rouvrez l’application avec une connexion.'));
} else {
  offlineStatus('Le mode hors connexion nécessite un navigateur compatible et une adresse HTTPS.');
}
