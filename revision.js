'use strict';

let revision;
let revisionObservations;

async function loadRevision() {
  const response = await fetch('./revision-2026-11-27.json');
  if (!response.ok) throw new Error('Catalogue indisponible');
  const data = await response.json();
  if (data.schema !== 'examen-civique-cr.supplement' || data.versionSchema !== 1 ||
      !Array.isArray(data.questions) || !data.questions.length || !Array.isArray(data.astuces)) {
    throw new Error('Format du catalogue inconnu');
  }
  revision = data;
  revisionObservations = new Map(data.observationsPubliques.map(note => [note.id, note]));
}

function revisionLink(url, label) {
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
}

function revisionSources(ids) {
  return ids.map(id => {
    const source = revision.sources[id];
    return revisionLink(source.url, `${source.organisme} · ${source.titre}`);
  }).join('<br>');
}

function revisionObservationLink(note) {
  const date = note.datePublication.split('-').reverse().join('/');
  return revisionLink(revision.sources[note.sourceId].url, `Retour public · ${note.mentionDeclaree} · ${date}`);
}

function revisionSourceHtml(q) {
  const label = q.kind === 'M' ? 'Mise en situation reconstituée' : 'Variante de connaissance rédigée pour la révision';
  const records = q.observationsPubliques.map(ref => revisionObservationLink(revisionObservations.get(ref.id)));
  const attribution = records.length
    ? `<p>Origine du thème&nbsp;: ${records.join(' · ')}. La formulation est pédagogique.</p>`
    : `<p>Exercice rédigé à partir de sources institutionnelles. ${revisionLink(q.repereProgramme.url, 'Programme de formation civique')}</p>`;
  return `<div class="sourcebox"><span class="badge blue">${label}</span>${attribution}</div>`;
}

function revisionEvidenceHtml(q) {
  const notes = q.observationsPubliques.map(ref => {
    const note = revisionObservations.get(ref.id);
    return `<p>${revisionObservationLink(note)}<br>${esc(ref.lienAvecExercice)}</p>`;
  }).join('');
  return `<div class="evidence"><div class="evidence-title">Point du programme · ${esc(q.repereProgramme.intitule)}</div>
    <p>${esc(q.specificRef.summary)}</p><p><strong>À retenir</strong> · ${esc(q.piege)}</p>
    <div class="evidence-links">${revisionLink(q.repereProgramme.url, 'Fiche du programme')}<br>${revisionSources(q.preuves)}</div>
    ${notes ? `<details class="revision-notes"><summary>Origine de l’exercice</summary>${notes}</details>` : ''}</div>`;
}

function startRevisionSeries(kind) {
  const questions = revision.questions.filter(q => q.kind === kind);
  const title = kind === 'M' ? 'Mises en situation' : 'Connaissances';
  startQuiz(shuffle(questions), `Révision complémentaire · ${title}`, false, false);
}

function singleRevisionButton(q) {
  return `<button type="button" class="back" data-revision-question="${esc(q.id)}">${esc(q.titre)}</button>`;
}

function renderRevision() {
  const labels = {M: 'Mises en situation', K: 'Connaissances'};
  document.getElementById('revision-index').innerHTML = Object.entries(labels).map(([kind, label]) =>
    `<div><h3>${label}</h3><ul>${revision.questions.filter(q => q.kind === kind).map(q =>
      `<li>${singleRevisionButton(q)}</li>`).join('')}</ul></div>`).join('');
  document.getElementById('revision-tips').innerHTML = revision.astuces.map(tip => {
    const exercises = tip.exercicesAssocies.map(id => singleRevisionButton(QUESTION_MAP.get(id)));
    const observations = tip.observationsPubliques.map(id => revisionObservationLink(revisionObservations.get(id)));
    return `<details class="review"><summary>${esc(tip.titre)}</summary><div class="review-body"><p>${esc(tip.action)}</p>
      <p><strong>Exemple</strong> · ${esc(tip.exemple)}</p>
      <p>À pratiquer · ${exercises.join(' · ')}</p>
      <div class="evidence-links">${revisionSources(tip.preuves)}${observations.length ? '<br>' + observations.join('<br>') : ''}</div></div></details>`;
  }).join('');
  document.getElementById('revision-bibliography').innerHTML = revision.observationsPubliques.map(note =>
    `<div class="resource">${revisionObservationLink(note)}<p>${esc(note.observation)}</p><p>${esc(note.limite)}</p></div>`).join('');
}

document.getElementById('revision').addEventListener('click', event => {
  const button = event.target.closest('[data-revision-question]');
  if (!button) return;
  const question = QUESTION_MAP.get(button.dataset.revisionQuestion);
  if (question) startQuiz([question], `Révision complémentaire · ${question.titre}`, false, false);
});
