let calendarioDashboard = null;

async function cargarCalendario() {
    const respuesta = await fetch('/api/events');
    if (!respuesta.ok) throw new Error('No se pudo cargar el calendario');
    const contenido = await respuesta.json();
    if (contenido.status !== 'success') throw new Error(contenido.message);

    const calendarEl = document.getElementById('calendar');
    if (!calendarioDashboard) {
        calendarioDashboard = new FullCalendar.Calendar(calendarEl, {
            initialView: 'dayGridMonth',
            locale: 'es',
            height: 'auto',
            dayMaxEvents: true,
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'dayGridMonth,timeGridWeek,listMonth'
            },
            buttonText: {
                today: 'Hoy',
                month: 'Mes',
                week: 'Semana',
                list: 'Lista'
            },
            events: contenido.data
        });
        calendarioDashboard.render();
    } else {
        calendarioDashboard.removeAllEvents();
        calendarioDashboard.addEventSource(contenido.data);
    }

    document.getElementById('total-eventos').textContent = contenido.count;
    return contenido;
}

function actualizarTamanoCalendario() {
    if (calendarioDashboard) calendarioDashboard.updateSize();
}
