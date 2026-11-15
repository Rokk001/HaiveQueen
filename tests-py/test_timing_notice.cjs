const assert=require('node:assert/strict');
const {testTimingNotice}=require('../haivequeen/web-client.js');
class Node {constructor(tag){this.tag=tag;this.children=[];this.textContent='';} append(...items){this.children.push(...items);} setAttribute(){} }
const doc={createElement:tag=>new Node(tag)};
const flatten=n=>[n.textContent,...n.children.map(flatten)].join(' ');
const text='12.30s call tests/test_a.py::test_a\n0.20s call tests/test_b.py::test_b\n15.000s test_c (tests.TestC.test_c)\nsource/tests/check.py exit=0 seconds=11.2\n100s arbitrary status\n';
let notice=testTimingNotice(doc,x=>x,text,10);
assert.match(flatten(notice),/3/);assert.match(flatten(notice),/test_a/);assert.doesNotMatch(flatten(notice),/test_b|arbitrary/);
assert.match(flatten(testTimingNotice(doc,x=>x,text,14)),/test_c/);
assert.doesNotMatch(flatten(testTimingNotice(doc,x=>x,text,14)),/test_a/);
assert.match(flatten(testTimingNotice(doc,x=>x,'Ran 8 tests in 200s',10)),/No individual test durations/);
assert.match(flatten(testTimingNotice(doc,x=>x,'0.20s call test.py::test_ok',10)),/No tests above/);
assert.match(flatten(testTimingNotice(doc,x=>x,'12.3s call <script>alert(1)<\/script>',10)),/<script>/);
assert.equal(notice.tag,'details');
console.log('Test timing notice checks passed');
