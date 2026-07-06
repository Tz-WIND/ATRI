import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node_script(script: str) -> str:
    node_path = shutil.which("node")
    assert node_path is not None, "node executable is required for chat math renderer tests"

    result = subprocess.run(  # noqa: S603 - node path is resolved and argv is test-owned.
        [str(Path(node_path).resolve()), "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
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
if (!html.includes('class="katex-display"')) throw new Error(html)
if (!html.includes('<msup><mi>x</mi><mi>n</mi></msup>')) throw new Error(html)
if (!html.includes('<mfrac>')) throw new Error(html)
if (!html.includes('≠')) throw new Error(html)

console.log('ok')
"""
    assert run_node_script(script).strip() == "ok"


def test_chat_math_renderer_renders_bracket_and_parenthesis_latex_delimiters():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const displayHtml = renderMarkdownWithMath(
  String.raw`\[x^2\]`,
  (markdown) => markdown,
)
const inlineHtml = renderMarkdownWithMath(
  String.raw`Use \(x^2\) here`,
  (markdown) => markdown,
)

if (!displayHtml.includes('class="math math-display"')) throw new Error(displayHtml)
if (!displayHtml.includes('class="katex-display"')) throw new Error(displayHtml)
if (!displayHtml.includes('<msup><mi>x</mi><mn>2</mn></msup>')) throw new Error(displayHtml)
if (!inlineHtml.includes('class="math math-inline"')) throw new Error(inlineHtml)
if (!inlineHtml.includes('class="katex"')) throw new Error(inlineHtml)
if (!inlineHtml.includes('<msup><mi>x</mi><mn>2</mn></msup>')) throw new Error(inlineHtml)

console.log('ok')
"""
    assert run_node_script(script).strip() == "ok"


def test_chat_math_renderer_renders_text_and_xrightarrow():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const html = renderMarkdownWithMath(
  String.raw`\[(R)\text{-2-丁醇} \xrightarrow{SOCl_2 / pyridine} (S)\text{-2-氯丁烷}\]`,
  (markdown) => markdown,
)

if (!html.includes('class="math math-display"')) throw new Error(html)
if (!html.includes('<mtext>-2-丁醇</mtext>')) throw new Error(html)
if (!html.includes('<mtext>-2-氯丁烷</mtext>')) throw new Error(html)
if (!html.includes('<mover><mo stretchy="true"')) throw new Error(html)
if (!html.includes('>→<')) throw new Error(html)
if (!html.includes('<msub><mi>l</mi><mn>2</mn></msub>')) throw new Error(html)
if (html.includes('>text<')) throw new Error(html)
if (html.includes('>xrightarrow<')) throw new Error(html)

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


def test_chat_math_renderer_renders_mathrm_chemistry_notation():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const html = renderMarkdownWithMath(
  String.raw`$$\mathrm{Fe^{2+}} - \mathrm{O} - \mathrm{Fe^{3+}}$$`,
  (markdown) => markdown,
)

if (!html.includes('class="math math-display"')) throw new Error(html)
if (html.includes('<mi>mathrm</mi>')) throw new Error(html)
if (!html.includes('<mi mathvariant="normal">F</mi>')) throw new Error(html)
if (!html.includes('<mi mathvariant="normal">O</mi>')) throw new Error(html)
if (!html.includes('<msup><mi mathvariant="normal">e</mi>')) throw new Error(html)
if (!html.includes('<mn>2</mn><mo>+</mo>')) throw new Error(html)
if (!html.includes('<mn>3</mn><mo>+</mo>')) throw new Error(html)

console.log('ok')
"""
    assert run_node_script(script).strip() == "ok"


def test_chat_math_renderer_does_not_expose_common_latex_command_names():
    script = r"""
import { renderMarkdownWithMath } from './frontend/src/components/chat/mathRenderer.js'

const html = renderMarkdownWithMath(
  String.raw`$$O_{\mathrm{lattice}} \rightarrow O_2 \uparrow + V_\mathrm{O}$$

$$R\bar{3}m \rightarrow Fm\bar{3}m$$`,
  (markdown) => markdown,
)

if (!html.includes('class="math math-display"')) throw new Error(html)
if (html.includes('>rightarrow<')) throw new Error(html)
if (html.includes('>uparrow<')) throw new Error(html)
if (html.includes('>bar<')) throw new Error(html)
if (html.includes('>mathrm<')) throw new Error(html)

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
