# Melhorias no pipeline de alerta de dengue

## Objetivo

Ampliar o pipeline existente com variáveis meteorológicas, dependência espacial entre municípios, métricas por estado e busca mais ampla de hiperparâmetros de árvores, mantendo a validação temporal e o teste final sem vazamento.

## Escopo aprovado

1. **Clima por município e semana**
   - Buscar dados meteorológicos diários NASA POWER/MERRA-2 por regiões e anos, com cache local retomável.
   - Incorporar precipitação acumulada, temperatura média e umidade relativa média semanal.
   - Para prever a semana seguinte, usar apenas clima da semana observada e resumos anteriores. Não usar clima futuro nem previsão meteorológica retrospectiva como se estivesse disponível no instante da previsão.
   - Registrar fonte, período, variáveis e grade aproximada de 0,5° × 0,625° no relatório.

2. **Dependência espacial**
   - Construir matriz de vizinhança municipal a partir da malha municipal mais recente do IBGE, com contiguidade Queen e pesos normalizados por linha.
   - Criar defasagem espacial de casos observados na semana corrente (`W*y`), sem incorporar o alvo da semana seguinte.
   - Comparar LightGBM com e sem a variável espacial, mantendo demais condições iguais. Interpretar como ganho preditivo, sem alegação causal.

3. **Métricas estaduais**
   - Preservar métricas anuais existentes e gerar MAE, RMSE, WAPE e captura top 10% por UF, ano e modelo.
   - Manter a exclusão do Espírito Santo desde 2020 já aplicada no projeto e deixar essa limitação explícita nos resultados.

4. **Busca de hiperparâmetros**
   - Substituir a busca curta por 12 configurações aleatórias e reproduzíveis de LightGBM.
   - Selecionar configurações pela WAPE média em validação temporal expansiva: treinar até 2020 e validar 2021; até 2021 e validar 2022; até 2022 e validar 2023.
   - Retreinar a configuração selecionada até 2023 e manter 2024–2025 como teste final intocado.

## Fluxo de dados

1. A referência municipal recebe coordenadas compatíveis com o código IBGE.
2. O coletor obtém clima NASA POWER diário em tiles regionais de até 10° e fatias anuais de até 366 dias, associa municípios à célula de grade mais próxima, agrega por município e semana e salva cache reutilizável.
3. A malha do IBGE gera a matriz Queen; a série municipal gera defasagens espaciais semanais.
4. O painel une as variáveis climáticas e espaciais usando município e semana.
5. Modelos base e ampliado são avaliados nos mesmos recortes temporais; métricas são gravadas no nível anual e estadual.
6. A busca seleciona hiperparâmetros nos anos de validação e só então ajusta o modelo final antes do teste reservado.

## Limitações e decisões

- NASA POWER/MERRA-2 representa dados meteorológicos em grade de 0,5° de latitude × 0,625° de longitude, não medição pontual municipal; a resolução pode suavizar variação intramunicipal. A malha IBGE vigente pode divergir dos limites municipais históricos.
- A API regional limita cada consulta a uma variável, 366 dias e uma área de até 10° por lado. O cache de tile/ano/variável permite retomada sem refazer blocos já concluídos.
- Municípios sem vizinhos contíguos precisam de tratamento determinístico e documentado; não podem receber um valor espacial inventado.
- A defasagem `W*y` é uma feature espacial preditiva, não um modelo SAR/SEM completo nem estimativa causal.
- Métricas por UF em volume desigual podem variar por tamanho estadual; incluir contagem de municípios e casos junto às métricas.
- A busca com múltiplos anos de validação aumenta o tempo de execução; semente fixa e resultados por candidato devem ser salvos.

## Critérios de sucesso

- Painel de features contém clima e defasagem espacial para linhas elegíveis, sem usar qualquer dado posterior à semana da previsão.
- Cache permite repetir construção do painel sem refazer downloads já concluídos.
- Métricas estaduais cobrem os UFs presentes e mantêm o filtro existente do ES.
- Busca registra cada configuração e desempenho por ano de validação; seleção usa somente validação.
- Teste 2024–2025 segue reservado e comparável com os baselines existentes.
- Relatório explica fontes, limitações, mudanças de avaliação e comparação com o baseline sem as novas features.
