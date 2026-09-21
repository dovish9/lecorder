import {test} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeMarkdown} from '../web/static/note-markdown.js';
import katex from '../web/static/vendor/katex/katex.mjs';
test('legacy JSON escapes become valid math without damaging TeX row breaks',()=>{
 const text=normalizeMarkdown('제목\\n\\n$'+ '\b'+'ar{X} + '+ '\f'+'rac{1}{2} + \\\\mu$');
 assert.ok(text.startsWith('제목\n\n'));
 katex.renderToString(text.split('$')[1],{throwOnError:true});
 const aligned=String.raw`$$\begin{aligned}a&=b\\c&=d\end{aligned}$$`;
 assert.equal(normalizeMarkdown(aligned),aligned);
 assert.equal(normalizeMarkdown(String.raw`$\nu+\nabla f$`),String.raw`$\nu+\nabla f$`);
});
