// 把 md_to_replay 生成时落到末尾的「大盘结构分析（当日）」段，搬到 legacy anchor
// `<!-- ========== 大盘结构分析 ========== -->` 之后（否则该锚点块为空 → 看板段二丢内容）
const fs = require('fs');
const src = process.argv[2];
const t = fs.readFileSync(src, 'utf8');

const tagA = '<!-- ========== 大盘结构分析 ========== -->';
const tagB = '<!-- ========== 大盘结构分析（当日） ========== -->';
const iB = t.indexOf(tagB);
if (iB < 0) { console.log('未找到尾部大盘结构段，跳过'); process.exit(0); }

// 尾部块：从 tagB 到 footer 之前
const iFoot = t.indexOf('<div class="footer">', iB);
const block = t.slice(iB + tagB.length, iFoot >= 0 ? iFoot : t.length).replace(/^\s+|\s+$/g, '');
const head = t.slice(0, iB).replace(/\s+$/, '');
const foot = iFoot >= 0 ? t.slice(iFoot) : '';

const iA = head.indexOf(tagA);
if (iA < 0) { console.log('未找到 legacy 锚点，跳过'); process.exit(0); }
const cut = iA + tagA.length;
const out = head.slice(0, cut) + '\n' + block + '\n' + head.slice(cut) + '\n' + foot;

fs.writeFileSync(src, out, 'utf8');
console.log('已搬移：' + Buffer.byteLength(out) + ' 字节');
const re = /<!-- =+ (.+?) =+ -->/g; let m;
while ((m = re.exec(out))) console.log(String(m.index).padStart(6) + '  ' + m[1]);
