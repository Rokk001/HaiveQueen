const assert = require('node:assert/strict');
const {dashboardPlanHeight} = require('../haivequeen/web-client.js');
const layout = {available:800, planChrome:70, gap:10, header:35, rows:[45,45,45,45,45,45], hiveHeight:300};
assert.equal(dashboardPlanHeight(layout),306,'Sparse Hive leaves room for all six plans');
assert.equal(dashboardPlanHeight({...layout,hiveHeight:520}),200,'Plans use remaining space');
assert.equal(dashboardPlanHeight({...layout,hiveHeight:900}),171,'Busy Hive keeps three plans visible');
assert.equal(dashboardPlanHeight({...layout,rows:[45,60],hiveHeight:900}),141,'Fewer than three plans keep their actual height');
assert.equal(dashboardPlanHeight({...layout,rows:[45,60,70,45],hiveHeight:900}),211,'Minimum uses actual row heights');
console.log('Dashboard height allocation passed');
