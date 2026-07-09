let resultadosCache = [];

function crearCelda(texto, clase) {
    const celda = document.createElement('td');
    if (clase) celda.className = clase;
    celda.textContent = texto;
    return celda;
}

function formatearFecha(valor) {
    if (!valor) return 'Sin fecha';
    const fecha = new Date(valor);
    if (Number.isNaN(fecha.getTime())) return valor;
    return new Intl.DateTimeFormat('es-CR', {
        dateStyle: 'medium',
        timeStyle: 'short'
    }).format(fecha);
}

function formatearPrecio(valor) {
    return valor || 'No disponible';
}

function renderizarResultados(filtro = '') {
    const tabla = document.getElementById('tabla-resultados');
    const vacio = document.getElementById('results-empty');
    const termino = filtro.trim().toLocaleLowerCase('es');
    const resultados = resultadosCache.filter((item) =>
        String(item.titulo || '').toLocaleLowerCase('es').includes(termino)
    );

    tabla.replaceChildren();
    resultados.forEach((item) => {
        const fila = document.createElement('tr');

        const producto = document.createElement('td');
        const titulo = document.createElement('span');
        titulo.className = 'product-title';
        titulo.textContent = item.titulo || 'Sin título';
        titulo.title = item.titulo || '';
        const descripcion = document.createElement('span');
        descripcion.className = 'product-description';
        descripcion.textContent = item.descripcion || 'Sin descripción';
        descripcion.title = item.descripcion || '';
        producto.append(titulo, descripcion);

        fila.appendChild(producto);
        fila.appendChild(crearCelda(formatearPrecio(item.precio)));
        fila.appendChild(
            crearCelda(item.calificacion ? `${item.calificacion} / 5` : 'N/D')
        );

        const origen = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = 'source-badge';
        badge.textContent = item.tipo_scraping || 'desconocido';
        origen.appendChild(badge);
        fila.appendChild(origen);

        fila.appendChild(crearCelda(formatearFecha(item.ultima_actualizacion)));

        const accion = document.createElement('td');
        if (item.enlace_archivo) {
            const enlace = document.createElement('a');
            enlace.className = 'icon-link';
            enlace.href = item.enlace_archivo;
            enlace.target = '_blank';
            enlace.rel = 'noopener noreferrer';
            enlace.title = 'Abrir fuente';
            enlace.setAttribute('aria-label', 'Abrir fuente');
            const icono = document.createElement('i');
            icono.className = 'bi bi-box-arrow-up-right';
            enlace.appendChild(icono);
            accion.appendChild(enlace);
        }
        fila.appendChild(accion);
        tabla.appendChild(fila);
    });

    vacio.classList.toggle('d-none', resultados.length > 0);
}

async function cargarResultadosScraping() {
    const respuesta = await fetch('/api/results');
    if (!respuesta.ok) throw new Error('No se pudieron cargar los resultados');
    const contenido = await respuesta.json();
    if (contenido.status !== 'success') throw new Error(contenido.message);

    resultadosCache = contenido.data;
    document.getElementById('total-registros').textContent = contenido.count;
    document.getElementById('total-fuentes').textContent =
        new Set(contenido.data.map((item) => item.tipo_scraping)).size;
    if (contenido.generated_at) {
        document.getElementById('ultima-actualizacion').textContent =
            `Datos actualizados: ${formatearFecha(contenido.generated_at)}`;
    }
    renderizarResultados(document.getElementById('results-search').value);
    return contenido;
}
