#!/usr/bin/env bash
# Deploy OceanShare web to Vercel
#
# Primeira vez: ele vai pedir login e te perguntar o nome do projeto.
# Depois disso, a cada execução ele atualiza o mesmo projeto.
#
# Pré-requisitos: Node.js instalado (tem npx). Se não tem:
#   macOS: brew install node  (ou baixar de nodejs.org)

set -e

cd "$(dirname "$0")/web"

echo "🌊 Enviando OceanShare pra Vercel..."
echo ""

# Deploy em produção. Na primeira vez, ele faz login e cria o projeto.
npx -y vercel@latest --prod --yes

echo ""
echo "✓ Deploy terminado. URL de produção está logo acima."
