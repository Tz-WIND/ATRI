import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node_script(script: str) -> str:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_chat_math_renderer_renders_display_latex_to_mathml():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const html = renderMarkdownWithMath(
  String.raw`$$\int x^n , dx = \frac{x^{n+1}}{n+1} + C \quad (n \neq -1)$$`,
  (markdown) => `<p>${markdown}</p>`,
)

if (!html.includes('class="math math-display"')) throw new Error(html)
if (!html.includes('<math display="block"')) throw new Error(html)
if (!html.includes('<msup><mi>x</mi><mi>n</mi></msup>')) throw new Error(html)
if (!html.includes('<mfrac>')) throw new Error(html)
if (!html.includes('&#x2260;')) throw new Error(html)

console.log('ok')
"""
    assert run_node_script(script).strip() == "ok"


def test_chat_math_renderer_leaves_fenced_code_blocks_alone():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const html = renderMarkdownWithMath(
  '```tex\n$$x^n$$\n```',
  (markdown) => markdown,
)

if (html.includes('class="math')) throw new Error(html)
if (!html.includes('$$x^n$$')) throw new Error(html)

console.log('ok')
"""
    assert run_node_script(script).strip() == "ok"


def test_chat_math_renderer_leaves_inline_code_alone():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const html = renderMarkdownWithMath(
  'Use `$x^n$` literally',
  (markdown) => markdown,
)

if (html.includes('class="math')) throw new Error(html)
if (!html.includes('$x^n$')) throw new Error(html)

console.log('ok')
"""
    assert run_node_script(script).strip() == "ok"
