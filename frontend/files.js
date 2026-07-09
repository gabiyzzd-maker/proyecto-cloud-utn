function formatearTamano(bytes) {
    const valor = Number(bytes);
    if (!Number.isFinite(valor) || valor < 0) return 'N/D';
    if (valor < 1024) return `${valor} B`;
    if (valor < 1024 * 1024) return `${(valor / 1024).toFixed(1)} KB`;
    return `${(valor / (1024 * 1024)).toFixed(1)} MB`;
}

async function cargarArchivos() {
    const respuesta = await fetch('/api/files');
    if (!respuesta.ok) throw new Error('No se pudieron cargar los archivos');
    const contenido = await respuesta.json();
    if (contenido.status !== 'success') throw new Error(contenido.message);

    const tabla = document.getElementById('tabla-archivos');
    const vacio = document.getElementById('files-empty');
    tabla.replaceChildren();

    contenido.data.forEach((item) => {
        const fila = document.createElement('tr');
        fila.appendChild(crearCelda(item.nombre_archivo || 'Sin nombre'));
        fila.appendChild(crearCelda(item.tipo_contenido || 'N/D'));
        fila.appendChild(crearCelda(formatearTamano(item.tamano_bytes)));

        const hash = document.createElement('td');
        const hashTexto = document.createElement('span');
        hashTexto.className = 'hash-value';
        hashTexto.textContent = item.hash_sha256 || 'N/D';
        hashTexto.title = item.hash_sha256 || '';
        hash.appendChild(hashTexto);
        fila.appendChild(hash);

        fila.appendChild(crearCelda(formatearFecha(item.ultima_verificacion)));

        const accion = document.createElement('td');
        if (item.download_url) {
            const enlace = document.createElement('a');
            enlace.className = 'icon-link';
            enlace.href = item.download_url;
            enlace.target = '_blank';
            enlace.rel = 'noopener noreferrer';
            enlace.title = 'Ver archivo';
            enlace.setAttribute('aria-label', 'Ver archivo');
            const icono = document.createElement('i');
            icono.className = 'bi bi-eye';
            enlace.appendChild(icono);
            accion.appendChild(enlace);
        }
        fila.appendChild(accion);
        tabla.appendChild(fila);
    });

    document.getElementById('total-archivos').textContent = contenido.count;
    vacio.classList.toggle('d-none', contenido.count > 0);
    return contenido;
}
