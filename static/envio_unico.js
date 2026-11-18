/* Envio único: um formulário com data-envio-unico não sai duas vezes (duplo clique) e volta a funcionar
   quando o navegador restaura a página da cache ao voltar atrás (bfcache). */
(function () {
  var formularios = document.querySelectorAll('form[data-envio-unico]');
  formularios.forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (f.dataset.enviado) { e.preventDefault(); return; }
      f.dataset.enviado = '1';
      var b = f.querySelector('button[type=submit]');
      if (b) { setTimeout(function () { b.disabled = true; }, 0); }
    });
  });
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) { return; }
    formularios.forEach(function (f) {
      delete f.dataset.enviado;
      f.querySelectorAll('button[type=submit]').forEach(function (b) { b.disabled = false; });
    });
  });
})();
