import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createLibrary} from '../../web/static/dashboard/library.js';

class Element {
  children=[];value='';dataset={};classList={toggle(){}};
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(){}
  querySelector(){return null;}
  querySelectorAll(){return [];}
}
const contents = element => [element.textContent||'',...element.children.map(contents)].join(' ');
test('library follows browsing course while capture course and its badge stay independent',()=>{
  globalThis.document={createElement:()=>new Element()};
  globalThis.window={history:{replaceState(){}}};
  const app={activeCourseId:2,recordingCourseId:1,recordingState:'recording',storageItems:[],
    recordings:[{id:'a',title:'물리 녹음',course_id:1,status:'failed'},
      {id:'b',title:'통계 녹음',course_id:2,status:'completed'},
      {id:'c',title:'미지정 녹음',course_id:0,status:'completed'}]};
  const ui=Object.fromEntries(['librarySort','librarySearch','libraryFilter','libraryList','libraryDetail','libraryBadge','dockCourseLabel','dockCourseMenu'].map(key=>[key,new Element()]));
  ui.libraryFilter.value='all';ui.librarySort.value='name-asc';ui.workspacePanels=[];
  ui.dockCourseLabel.parentElement=new Element();
  const library=createLibrary({app,ui,actions:{},listen(){}});
  for(const state of ['recording','paused','finalizing','idle']){
    app.recordingState=state;app.activeCourseId=2;
    library.renderLibrary();
    assert.match(contents(ui.libraryList),/통계 녹음/);
    assert.doesNotMatch(contents(ui.libraryList),/물리 녹음/);
    assert.equal(app.recordingCourseId,1);
    app.activeCourseId=0;
    library.renderLibrary();
    assert.match(contents(ui.libraryList),/미지정 녹음/);
  }
  app.recordingState='recording';app.activeCourseId=2;
  ui.dockCourseLabel.parentElement.children=[];
  library.renderCourseAlertBadges();
  assert.match(contents(ui.dockCourseLabel.parentElement),/1/);
});

test('opening another course recording during capture changes only browsing selection', async()=>{
  const {createRouting}=await import('../../web/static/dashboard/routing.js');
  globalThis.window={location:{hash:'#/recordings/job/b'},localStorage:{setItem(){}},history:{replaceState(){}}};
  const entry={key:'job:b',kind:'job',recording:{id:'b',course_id:2}};
  const app={activeCourseId:1,recordingCourseId:1,recordingState:'recording',libraryEntries:[entry]};
  const ui={workspaceTabs:[],workspacePanels:[],workspaceTitle:{},workspaceDescription:{}};
  const actions={refreshStorage(){},async selectCourse(id){app.activeCourseId=id;},renderLibrary(){}};
  await createRouting({app,ui,actions,listen(){}}).applyRoute();
  assert.equal(app.activeCourseId,2);assert.equal(app.recordingCourseId,1);assert.equal(app.selectedLibraryKey,'job:b');
});
