import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";

// Métrica personalizada para contar errores en la respuesta (status ≠ 200)
export let errorRate = new Rate("errors");

// === OPCIONES DE CARGA ===
export let options = {
    // Ignorar certificados TLS autofirmados (porque accedes a https://10.10.10.34)
    insecureSkipTLSVerify: true,

    // Fases de carga: ramp-up → steady-state → ramp-down
    stages: [
        { duration: "20s", target: 10 },  // Subimos de 0 a 10 VUs en 20s
        { duration: "40s", target: 10 },  // Mantenemos 10 VUs durante 40s
        { duration: "20s", target: 0 },   // Bajamos de 10 a 0 en 20s
    ],

    // Umbrales (thresholds) que queremos cumplir para considerar la prueba exitosa
    thresholds: {
        // Menos del 5% de errores (status ≠ 200)
        "errors": ["rate<0.05"],
        // Queremos que el 95% de las peticiones respondan en < 2000 ms
        "http_req_duration": ["p(95)<2000"],
    },
};

// URL base de tu aplicación
const BASE_URL = "https://10.10.10.34";

// Array con los nombres exactos que aparecen en los botones de tu dashboard.
// Si tu dashboard tiene más clientes, agrégalos aquí con su texto exactamente igual.
const CLIENTES = [
    "CTRE HOSP UNIVERSITAIRE DE MONTPELLIER",
    "ORANGE APPLICATIONS FOR BUSINESS",
    "VERIFONE SYSTEMS FRANCE SAS",
    // Si tienes más, por ejemplo:
    // "OTRO CLIENTE EJEMPLO 1",
    // "OTRO CLIENTE EJEMPLO 2",
];

export default function () {
    // 1. Simular carga de la página principal (opcional):
    //    Si quieres simular que primero el usuario carga "/", descomenta estas líneas:
    //
    // let home = http.get(`${BASE_URL}/`, { tags: { name: "GET / (Home)" } });
    // check(home, {
    //     "home status es 200": (r) => r.status === 200,
    // });
    // sleep(0.5); // espera media segundo antes de “hacer clic” en un botón

    // 2. Elegimos un cliente al azar para simular un clic en su botón
    let idx = Math.floor(Math.random() * CLIENTES.length);
    let nombreCliente = CLIENTES[idx];

    // Codificamos el nombre del cliente para usarlo en la URL (reemplaza espacios por %20, etc.)
    let clienteCodificado = encodeURIComponent(nombreCliente);

    // 3. Construimos la URL final: GET https://10.10.10.34/status/{clienteCodificado}
    let url = `${BASE_URL}/status/${clienteCodificado}`;

    // 4. Realizamos la petición GET
    let res = http.get(url, { tags: { name: `GET /status/${nombreCliente}` } });

    // 5. Verificamos que devuelva 200 OK y que el body no esté vacío
    let ok = check(res, {
        "status era 200": (r) => r.status === 200,
        "body no vacía": (r) => r.body && r.body.length > 0,
    });

    // Si no pasó los checks, lo contamos como error (para la métrica 'errors')
    if (!ok) {
        errorRate.add(1);
    }

    // 6. Esperamos 1 segundo antes de la próxima iteración
    sleep(1);
}
