# legacy/ — Referencia histórica

Este directorio conserva los artefactos originales del prototipo:

- `boom_crash_quant_analyzer.py`
- `analizador_crash_boom_quantitativo.ipynb`

Son la **referencia histórica** del comportamiento a migrar: fórmulas, parámetros
por defecto y semántica numérica que `src/quantlab/` debe reproducir.

**No deben editarse.** Su contenido está fijado por hash en
`tests/fixtures/legacy_sha256.json`; cualquier modificación rompe la verificación
de integridad del prototipo.
