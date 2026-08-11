"""Pruebas de la capa de reglas del pipeline.

Estas reglas son las que deciden si una detección del módulo llega a molestar a
un operador. No tenían ninguna prueba, y por eso pasó desapercibido durante
todo el desarrollo del módulo de identificación que la lista blanca de clases
—con un valor por defecto inventado, `["person"]`— descartaba la pregunta
"¿reconocés a esta persona?" sin evento, sin log y sin ninguna señal.

Lo que se protege acá:
  - que una alerta que el módulo confirmó llegue, aunque su clase no se llame
    como el pipeline supone;
  - que la lista blanca, cuando el módulo SÍ la declara, siga filtrando (es lo
    único que evita que "persona parada" entre en la cola de revisión de caídas);
  - que los módulos que confirman por su cuenta no queden atrapados por la
    persistencia, porque su alerta vive en un solo frame.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "apps" / "ai-worker"))
sys.path.insert(0, str(RAIZ / "packages" / "py-contracts"))

from percepta_contracts import Detection  # noqa: E402

from ai_worker.pipeline import CameraAssignment, CameraPipeline, _ModuleState  # noqa: E402


def deteccion(clase: str, conf: float = 0.9, confirmada: bool = True) -> Detection:
    return Detection(
        class_label=clase, class_id=0, confidence=conf, bbox=(0.1, 0.1, 0.2, 0.4),
        attributes={"kind": "alert", "confirmed": "true" if confirmada else "false"},
    )


def pipeline() -> CameraPipeline:
    """Un pipeline sin arrancar: sólo se ejercitan sus reglas."""
    a = CameraAssignment(camera_id="cam-1", site_id="sitio-1", organization_id="org-1", modules=[])
    return CameraPipeline(a, {}, media_url="http://x", event_url="http://x", token="t")


def modulo(clave: str, **cfg) -> dict:
    return {"moduleKey": clave, "eventType": "x", "severity": "low", "config": cfg}


# ═══════════════════════════════════════════════════════════════════

def test_la_pregunta_por_un_desconocido_llega():
    """El caso que estaba roto: la clase no se llama 'person'."""
    p = pipeline()
    dispara, fuertes = p._evaluate(
        modulo("person-identification", classes=["person.unknown"], minConfidence=0.45,
               minPersistenceFrames=1, cooldownSeconds=3),
        [deteccion("person.unknown", 0.88)], _ModuleState(), now=1000.0,
    )
    assert dispara, "una cara desconocida confirmada tiene que generar la pregunta"
    assert len(fuertes) == 1


def test_sin_lista_blanca_no_se_filtra_por_clase():
    """Antes se asumía `['person']`, y eso descartaba todo lo demás en silencio."""
    p = pipeline()
    dispara, _ = p._evaluate(
        modulo("cualquiera", minPersistenceFrames=1),
        [deteccion("lo.que.sea", 0.9)], _ModuleState(), now=1000.0,
    )
    assert dispara, "sin `classes` configurado, el módulo decide qué emite"


def test_la_lista_blanca_declarada_sigue_filtrando():
    """Es lo único que mantiene 'persona parada' fuera de la cola de caídas."""
    p = pipeline()
    dispara, fuertes = p._evaluate(
        modulo("fall-detection", classes=["fall"], minConfidence=0.5),
        [deteccion("person_standing", 0.95, confirmada=False)], _ModuleState(), now=1000.0,
    )
    assert not dispara and not fuertes


def test_una_caida_confirmada_no_espera_persistencia():
    """Su alerta vive en UN frame: exigirle tres es no emitirla nunca."""
    p = pipeline()
    dispara, _ = p._evaluate(
        modulo("fall-detection", classes=["fall"], minConfidence=0.5, minPersistenceFrames=5),
        [deteccion("fall", 0.7)], _ModuleState(), now=1000.0,
    )
    assert dispara


def test_sin_confirmar_si_espera_persistencia():
    p = pipeline()
    st = _ModuleState()
    cfg = modulo("x", minPersistenceFrames=3, minConfidence=0.4)
    dets = [deteccion("algo", 0.9, confirmada=False)]
    assert not p._evaluate(cfg, dets, st, 1000.0)[0]
    assert not p._evaluate(cfg, dets, st, 1000.3)[0]
    assert p._evaluate(cfg, dets, st, 1000.6)[0], "al tercer frame seguido sí"


def test_el_enfriamiento_no_deja_repetir():
    p = pipeline()
    st = _ModuleState()
    cfg = modulo("person-identification", classes=["person.unknown"], minPersistenceFrames=1,
                 cooldownSeconds=3)
    assert p._evaluate(cfg, [deteccion("person.unknown")], st, 1000.0)[0]
    st.last_event_ts = 1000.0
    assert not p._evaluate(cfg, [deteccion("person.unknown")], st, 1001.0)[0]
    assert p._evaluate(cfg, [deteccion("person.unknown")], st, 1004.0)[0], "pasado el enfriamiento sí"


def test_la_confianza_baja_no_pregunta():
    """Una cara borrosa da un recorte que nadie puede reconocer."""
    p = pipeline()
    dispara, _ = p._evaluate(
        modulo("person-identification", classes=["person.unknown"], minConfidence=0.45,
               minPersistenceFrames=1),
        [deteccion("person.unknown", 0.2)], _ModuleState(), now=1000.0,
    )
    assert not dispara


def test_el_descarte_por_lista_blanca_se_registra(capsys=None):
    """Un descarte invisible fue exactamente el problema. Ahora deja rastro."""
    import logging

    registros: list[str] = []

    class Espia(logging.Handler):
        def emit(self, record):  # noqa: D102
            registros.append(record.getMessage())

    log = logging.getLogger("pipeline")
    espia = Espia()
    log.addHandler(espia)
    try:
        pipeline()._evaluate(
            modulo("person-identification", classes=["person"]),
            [deteccion("person.unknown", 0.9)], _ModuleState(), now=1000.0,
        )
    finally:
        log.removeHandler(espia)

    assert any("person.unknown" in r and "classes" in r for r in registros), (
        f"el descarte tiene que decir qué clase se descartó; se registró: {registros}"
    )


# ── lo que el módulo declara vs lo que el worker adivinaba ──────────

def test_el_tipo_de_evento_sale_del_manifiesto():
    """'person-identification' daba 'person.detected', un evento que no emite."""
    from ai_worker.main import _tipo_declarado

    assert _tipo_declarado("person-identification", "type", "?") == "person.unknown"
    assert _tipo_declarado("person-identification", "defaultSeverity", "?") == "low"
    assert _tipo_declarado("fall-detection", "type", "?") == "person.fall"


def test_los_defaults_del_schema_se_aplican():
    """Una asignación con config vacía tiene que comportarse como la del formulario."""
    from ai_worker.main import _defaults_del_modulo

    d = _defaults_del_modulo("person-identification")
    assert d.get("classes") == ["person.unknown"], d
    assert d.get("minPersistenceFrames") == 1
    assert _defaults_del_modulo("fall-detection").get("classes") == ["fall"]


def test_una_asignacion_con_config_vacia_queda_usable():
    """Es la asignación que estaba rota: `{}` en la base, sin pasar por el formulario."""
    from ai_worker.main import preparar_modulo

    m = preparar_modulo({"moduleKey": "person-identification", "aiModuleId": "id-1", "config": {}})
    assert m["eventType"] == "person.unknown", m["eventType"]
    assert m["severity"] == "low"
    assert m["config"]["classes"] == ["person.unknown"]

    # Y con esa configuración, la pregunta efectivamente dispara.
    dispara, _ = pipeline()._evaluate(m, [deteccion("person.unknown", 0.88)], _ModuleState(), 1000.0)
    assert dispara, "una asignación recién creada tiene que poder alertar"


def test_el_manifiesto_cubre_al_modulo_que_no_declara_su_evento():
    """El caso donde adivinar por el nombre daba cualquier cosa.

    `helmet-detection` no pone `eventType` en su config.schema.json. La regla
    vieja partía el nombre por el guión y armaba 'helmet.detected'; el manifiesto
    dice 'ppe.helmet_missing' con severidad alta. Un evento con un tipo que no
    existe no lo reconoce ninguna pantalla ni ninguna notificación.
    """
    from ai_worker.main import _defaults_del_modulo, preparar_modulo

    assert "eventType" not in _defaults_del_modulo("helmet-detection"), (
        "si este módulo empieza a declarar su eventType, esta prueba deja de "
        "cubrir el camino del manifiesto: elegir otro que no lo declare"
    )
    m = preparar_modulo({"moduleKey": "helmet-detection", "aiModuleId": "id-2", "config": {}})
    assert m["eventType"] == "ppe.helmet_missing", m["eventType"]
    assert m["severity"] == "high", m["severity"]


def test_lo_que_configuro_la_camara_le_gana_al_modulo():
    from ai_worker.main import preparar_modulo

    m = preparar_modulo({
        "moduleKey": "person-identification", "aiModuleId": "id-1",
        "config": {"severity": "high", "minConfidence": 0.9},
    })
    assert m["severity"] == "high"
    assert m["config"]["minConfidence"] == 0.9
    assert m["config"]["matchThreshold"] == 0.42, "lo que la cámara no dice lo pone el módulo"


if __name__ == "__main__":
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"  OK    {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  FALLA {nombre}: {e}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"  ERROR {nombre}: {e!r}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas OK")
    sys.exit(1 if fallos else 0)
