function actualizarEstadoServidor(enLinea) {
    const texto = document.getElementById('api-status');
    const punto = document.getElementById('api-status-dot');
    texto.textContent = enLinea ? 'API en línea' : 'API sin conexión';
    punto.classList.toggle('online', enLinea);
    punto.classList.toggle('offline', !enLinea);
}

function mostrarError(error) {
    const alerta = document.getElementById('global-error');
    alerta.textContent = error.message || 'Ocurrió un error inesperado';
    alerta.classList.remove('d-none');
}

function ocultarError() {
    document.getElementById('global-error').classList.add('d-none');
}

async function cargarDashboard() {
    ocultarError();
    const boton = document.getElementById('refresh-button');
    boton.disabled = true;

    try {
        const estado = await fetch('/api/status');
        actualizarEstadoServidor(estado.ok);
        await Promise.all([
            cargarResultadosScraping(),
            cargarArchivos(),
            cargarCalendario()
        ]);
    } catch (error) {
        actualizarEstadoServidor(false);
        mostrarError(error);
        console.error('[Dashboard]', error);
    } finally {
        boton.disabled = false;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document
        .getElementById('refresh-button')
        .addEventListener('click', cargarDashboard);
    document
        .getElementById('results-search')
        .addEventListener('input', (evento) => renderizarResultados(evento.target.value));
    document
        .getElementById('calendar-tab')
        .addEventListener('shown.bs.tab', actualizarTamanoCalendario);

    cargarDashboard();
});
