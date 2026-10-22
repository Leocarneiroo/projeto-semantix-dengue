# Gráficos de clima e dengue

## Objetivo

Acrescentar ao painel uma explicação visual da relação entre as variáveis climáticas da NASA POWER e os casos de dengue observados por município.

## Desenho aprovado

Inserir uma seção “Clima e dengue” antes da seção de método, com um gráfico composto por três painéis pequenos: precipitação, temperatura e umidade relativa. Cada painel mostra a incidência média municipal nas quatro semanas posteriores, agrupada por oito faixas de exposição climática.

Os painéis usam pontos conectados, valores numéricos nos eixos e cores distintas já compatíveis com a paleta do painel. O eixo X indica a média da variável em cada faixa. O eixo Y indica casos por 100 mil habitantes. A legenda explica que cada exposição semanal é associada às semanas seguintes no mesmo município.

## Dados e cálculo

- Fontes: série semanal de casos prováveis do SINAN, clima semanal NASA POWER/MERRA-2 e população municipal do Censo 2022 já versionados no repositório.
- Chave de associação: código municipal do IBGE e semana de início.
- Exposição: precipitação acumulada semanal, temperatura média semanal e umidade relativa média semanal.
- Desfecho: soma de casos nas quatro semanas seguintes (`t+1` a `t+4`) dividida pela população de 2022 e multiplicada por 100 mil.
- Incluir municípios com população de pelo menos 20 mil habitantes, para manter consistência com o mapa existente.
- Usar semanas de 04/01/2015 a 30/11/2025, para que todas tenham quatro semanas futuras observadas.
- Casos ausentes numa semana municipal contam como zero. Semanas sem clima válido ficam fora dos gráficos.
- Em cada variável, ordenar as semanas municipais em oito grupos de exposição com contagens semelhantes. Cada ponto mostra a média das incidências municipais daquele grupo; não ajustar curva nem modelo causal.

A leitura atual dos arquivos resulta em 1.710 municípios e 974.700 combinações município-semana elegíveis, sem valores climáticos ausentes nessas linhas.

## Apresentação e acessibilidade

- Gerar uma figura estática a partir dos dados versionados, seguindo o padrão atual do painel.
- Incluir título, unidades, intervalo temporal, número de municípios e legenda curta com a janela de quatro semanas.
- Usar texto alternativo que descreva os três painéis e suas variáveis.
- Manter os painéis legíveis em tela estreita, empilhando-os quando necessário.
- Declarar que os resultados são associações exploratórias. Sazonalidade e diferenças regionais podem explicar parte do padrão; não inferir causalidade.
- Informar que NASA POWER/MERRA-2 fornece estimativas em grade meteorológica, não medições em cada município.

## Fora de escopo

- Refazer o modelo de previsão, métricas ou seleção de variáveis.
- Atribuir efeito independente ao clima ou gerar recomendações de alerta.
- Adicionar gráficos interativos, nova biblioteca de frontend ou dados baixados no navegador.

## Critérios de sucesso

1. O painel publicado contém os três painéis com valores derivados dos arquivos NASA/SINAN versionados.
2. Cada eixo apresenta unidades e os textos informam população de referência, janela de quatro semanas e período analisado.
3. Um comando local regenera a figura a partir dos arquivos processados, sem nova dependência nem consulta de rede quando os dados estão presentes.
4. A limitação de grade meteorológica e a natureza não causal da análise ficam visíveis junto ao gráfico.
5. A figura e o texto continuam legíveis em desktop e celular.
