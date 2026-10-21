# Melhorias no pipeline de dengue — Plano de implementação

> **For agentic workers:** Executar inline nesta sessão com `superpowers:executing-plans`. Tarefas em caixas para acompanhar.

**Goal:** Acrescentar clima semanal, dependência espacial municipal, métricas por UF e busca LightGBM com validação temporal expansiva.

**Architecture:** Separar coleta/agregação meteorológica e criação de vizinhança em módulo auxiliar; manter painel e avaliação no fluxo existente. Cachear dados por município-semana e pesos espaciais. O modelo base e o enriquecido compartilham os mesmos cortes temporais; seleção ocorre apenas em 2021–2023.

**Tech Stack:** Python, pandas, requests, LightGBM, Shapely 2 e SciPy sparse (já dependência transitiva do scikit-learn).

---

### Task 1: Dados climáticos e vizinhança IBGE

**Files:**
- Create: `src/environmental_features.py`
- Modify: `requirements.txt`
- Cache: `data/processed/municipality_week_weather_nasa_power.csv.gz`, `data/processed/municipal_neighbors.csv`

- [x] Obter malha municipal GeoJSON do IBGE; extrair geocódigo, ponto interno municipal e adjacência Queen.
- [x] Persistir pesos Queen normalizados por município, sem vizinhos para unidades isoladas.
- [x] Coletar `PRECTOTCORR`, `T2M` e `RH2M` pela API regional diária NASA POWER/MERRA-2; dividir em tiles de 10° e anos, com retry e cache por tile/ano/variável.
- [x] Agregar dias para semana iniciada no domingo, salvar cache atômico e reaproveitar cache existente.
- [x] Documentar cache, grade aproximada de 0,5° × 0,625° e limites da API.

### Task 2: Painel de features sem vazamento

**Files:**
- Modify: `src/analyze.py`

- [x] Acrescentar longitude/latitude à referência IBGE por ponto interno.
- [x] Unir clima por município e semana; features válidas na linha t usam somente dados até t.
- [x] Calcular `spatial_lag_cases` como matriz W vezes os casos da semana observada, por semana.
- [x] Manter baselines sem clima/espacial e expor lista enriquecida de features.
- [x] Aplicar missingness climática de forma explícita; não preencher ausência de fonte como zero.

### Task 3: Busca e avaliação comparável

**Files:**
- Modify: `src/compare_models.py`

- [x] Gerar 12 candidatos determinísticos de LightGBM.
- [x] Avaliar cada candidato em três folds expansivos (treino até 2020/21/22; validação em 2021/22/23) e selecionar pela WAPE média.
- [x] Ajustar base e enriquecido com parâmetros comuns; preservar 2024–2025 exclusivamente como teste.
- [x] Salvar previsões com UF/código municipal e métricas anual Brasil + por UF/ano/modelo, incluindo contagem de municípios/casos.
- [x] Gravar resultados por candidato e por fold; registrar busca, features e semente no sumário.

### Task 4: Relatório e verificação

**Files:**
- Modify: `README.md`, `relatorio.md`
- Check: sintaxe dos módulos e coerência dos arquivos gerados

- [x] Atualizar fontes, métodos, limitações e comandos de reprodução.
- [x] Explicar que W*y é feature preditiva, não estimativa causal nem SAR/SEM completo.
- [x] Validar sintaxe e revisar diff/arquivos de saída.

## Ordem e critérios

Executar tarefas 1→2→3→4. A ingestão deve ser reproduzível e cacheada; nenhuma feature pode usar semana futura; a seleção de hiperparâmetros deve ignorar 2024–2025; métricas estaduais devem identificar UF; o relatório deve distinguir clima reanalisado e dados disponíveis operacionalmente.
