'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const R = require('../app-transporte/recorridos.js');
const read = name => JSON.parse(fs.readFileSync(path.join(root, 'app-transporte/data', name)));
const schedule = read('horarios.json'), geo = read('cabeceras.json'), routes = read('recorridos.json');
const engine = R.create(schedule, geo, routes);

function fixture({time=480,days=[1,2,3,4,5],stops=null,notes=[]}={}) {
  const service={id:'fixture',corridor:'TEST',line:'A - D',nodes:['A','D'],direction:'I',time:R.clock(time).time,minutes:time,company:'TEST SA',cuit:'30-12345678-9',modality:'COMUN',route:'VIA B',service_days:days};
  const places=Object.fromEntries(['A','B','C','D'].map((id,i)=>[id,{id,name:id,aliases:[id],corridors:['TEST'],lat:-31-i/20,lon:-64,geo_source:'test'}]));
  const profile={id:'profile',issues:[],notes,stops:stops||[0,60,75,85].map((offset,i)=>({place_id:['A','B','C','D'][i],arrival_offset:offset,departure_offset:offset,rows:[i+2]}))};
  const data={schema_version:1,places,profiles:[profile],bindings:{[R.signature(service,{})]:profile.id},name_aliases:{}};
  return {service,profile,data,engine:R.create({services:[service]},{locations:{}},data)};
}
test('conserva las 5.504 salidas vigentes y coincide con el conteo auditado',()=>{
  assert.equal(engine.models.length,schedule.services.length);
  assert.equal(engine.query({}).length,schedule.services.length);
  assert.equal(engine.coverage.linked,routes.stats.linked_services);
});
test('todas las vinculaciones usan perfiles completos y tiempos crecientes',()=>{
  for(const model of engine.models.filter(m=>m.profile)) assert.ok(R.validProfile(model.profile,routes.places));
});
test('Salsipuedes → Agua de Oro: EDER usa la salida del PDF, no las 07:55 del Excel',()=>{
  const j=engine.query({origin:'loc-1177',destination:'loc-1248',company:'EDER SERVICIO DIFERENCIAL',day:1}).find(j=>j.service.time==='07:40');
  assert.ok(j);assert.equal(j.service.direction,'I');assert.equal(R.clock(j.boarding).time,'08:40');assert.equal(R.clock(j.arrival).time,'08:55');assert.equal(j.duration,15);
});
test('Ida y Vuelta tienen secuencias y tiempos distintos',()=>{
  const outgoing=engine.query({origin:'loc-1248',destination:'loc-1219',company:'EDER SERVICIO DIFERENCIAL',line:'CÓRDOBA - LA GRANJA'});
  const incoming=engine.query({origin:'loc-1219',destination:'loc-1248',company:'EDER SERVICIO DIFERENCIAL',line:'CÓRDOBA - LA GRANJA'});
  assert.ok(outgoing.length&&incoming.length);
  assert.ok(outgoing.every(j=>j.service.direction==='I'&&j.duration===10));
  assert.ok(incoming.every(j=>j.service.direction==='V'&&j.duration===15));
});
test('no confunde El Talar por Donato Álvarez con Padre Luchesse/Aeropuerto',()=>{
  const models=engine.models.filter(m=>m.service.line==='CÓRDOBA - EL TALAR');
  assert.ok(models.length);assert.ok(models.every(m=>!m.profile));
  const j=engine.query({origin:'loc-1598',destination:'loc-1',line:'CÓRDOBA - EL TALAR'});
  assert.ok(j.length);assert.ok(j.every(j=>j.service.direction==='V'&&j.arrival===null));
});
test('corredor y empresa limitan todas las líneas y localidades',()=>{
  const f={corridor:'SUR',company:'ALEJANDRO S.R.L'};
  const lines=engine.facet('line',f);assert.deepEqual(lines,['RÍO CUARTO - ALEJANDRO ROCA']);
  for(const origin of engine.facet('origin',f)) assert.ok(engine.query({...f,origin}).length);
  for(const destination of engine.facet('destination',f)) assert.ok(engine.query({...f,destination}).length);
});
test('el destino solo ofrece localidades posteriores al origen',()=>{
  const {engine:e}=fixture();assert.deepEqual(e.facet('destination',{origin:'C'}),['D']);assert.deepEqual(e.facet('origin',{destination:'B'}),['A']);
  assert.equal(e.query({origin:'C',destination:'B'}).length,0);assert.equal(e.query({origin:'B',destination:'B'}).length,0);
});
test('cambio de día: domingo de salida puede ser lunes de subida',()=>{
  const {engine:e}=fixture({time:1410,days:[7]});
  const j=e.query({origin:'B',destination:'C',day:1})[0];assert.ok(j);assert.deepEqual(j.days,[1]);assert.deepEqual(R.clock(j.boarding),{time:'00:30',dayOffset:1});
  assert.equal(e.query({origin:'B',destination:'C',day:7}).length,0);
  assert.deepEqual(e.facet('day',{origin:'B',destination:'C'}),['1']);
  assert.ok(e.facet('origin',{destination:'D',day:1}).includes('B'));
  assert.ok(!e.facet('origin',{destination:'D',day:1}).includes('A'));
});
test('un lunes nuevo cambia horas/días sin perder el recorrido',()=>{
  const f=fixture(),changed={...f.service,id:'next-week',minutes:540,time:'09:00',service_days:[6,7]};
  const e=R.create({services:[changed]},{locations:{}},f.data),j=e.query({origin:'B',destination:'C',day:6})[0];
  assert.ok(j.model.profile);assert.equal(R.clock(j.boarding).time,'10:00');assert.equal(j.service.time,'09:00');
});
test('una variante nueva o CUIT desconocido queda pendiente, sin estimaciones inventadas',()=>{
  const f=fixture();
  for(const change of [{route:'OTRA RUTA'},{cuit:'#N/D'},{direction:'V'},{company:'OTHER',cuit:'30-98765432-1'}]){
    const e=R.create({services:[{...f.service,...change}]},{locations:{}},f.data);
    assert.equal(e.coverage.linked,0);assert.equal(e.query({})[0].arrival,null);
  }
});
test('las localidades repetidas no se borran ni se fusionan a través del recorrido',()=>{
  const stops=[['A',0],['B',15],['C',30],['B',45],['D',60]].map(([place_id,n])=>({place_id,arrival_offset:n,departure_offset:n,rows:[]}));
  const {engine:e}=fixture({stops});const js=e.query({origin:'B',destination:'D'});
  assert.equal(js.length,2);assert.notEqual(js[0].key,js[1].key);assert.deepEqual(js.map(j=>j.duration),[45,15]);
});
test('si hay permanencia en una localidad, subida y bajada usan sus respectivos tiempos',()=>{
  const f=fixture();f.profile.stops[1].departure_offset=65;
  const e=R.create({services:[f.service]},{locations:{}},f.data);
  assert.equal(e.query({origin:'B',destination:'C'})[0].duration,10);
  assert.equal(e.query({origin:'A',destination:'B'})[0].duration,60);
});
test('coordenada ausente se conserva como hueco, no se elimina de la secuencia',()=>{
  const f=fixture();f.data.places.B.lat=null;f.data.places.B.lon=null;
  const e=R.create({services:[f.service]},{locations:{}},f.data),q=e.query({})[0];
  assert.equal(e.coordinates(q).length,4);assert.equal(e.coordinates(q)[1],null);assert.equal(q.model.stops.length,4);
});
test('observaciones no se convierten automáticamente en restricciones',()=>{
  const {engine:e}=fixture({notes:['No debería parar']});assert.equal(e.query({origin:'B',destination:'C'}).length,1);
});
test('un archivo de recorridos ausente no impide consultar los horarios',()=>{
  const e=R.create(schedule,geo,null);assert.equal(e.coverage.linked,0);assert.equal(e.query({}).length,schedule.services.length);assert.ok(e.query({}).every(j=>j.arrival===null));
});
test('búsqueda sin tildes encuentra localidades intermedias',()=>{
  assert.ok(engine.query({search:'salsipuedes',company:'EDER SERVICIO DIFERENCIAL'}).length);
  assert.equal(R.normalize('Córdoba – Río Ceballos'),'CORDOBA RIO CEBALLOS');
});
test('San Nicolás unifica la tilde y ofrece la línea Sarmiento por Colectora',()=>{
  assert.equal(R.normalize('San Nicolás'),R.normalize('San Nicolas'));
  const filters={origin:'loc-1',destination:'loc-1452',corridor:'PUNILLA',company:'EMPRESA SARMIENTO S.R.L.'};
  const line='CÓRDOBA - MALAGUEÑO - CARLOS PAZ';
  assert.ok(engine.facet('line',filters).includes(line));
  const journeys=engine.query({...filters,line});
  assert.equal(journeys.length,51);
  assert.ok(journeys.every(j=>j.service.direction==='I'&&j.model.profile&&j.model.profile.line.includes('Colectora')));
  assert.ok(journeys.every(j=>!R.normalize(j.service.route).includes('NO INGRESA')));
});
test('homónimos no se convierten en una misma parada',()=>{
  assert.notEqual(routes.places['loc-133'].id,routes.places['loc-6208'].id);
  assert.notEqual(engine.label('loc-133'),engine.label('loc-6208'));
});
test('el control distingue horario publicado de paso estimado y conserva el destino final',()=>{
  const service=engine.models.find(m=>m.service.company==='EDER SERVICIO DIFERENCIAL'&&m.service.line==='CÓRDOBA - LA GRANJA'&&m.service.time==='07:40'&&m.service.direction==='I').service;
  const published=engine.control({location:'loc-1',company:service.company,lines:[service.line]}).find(r=>r.service.id===service.id);
  const estimated=engine.control({location:'loc-1177',company:service.company,lines:[service.line]}).find(r=>r.service.id===service.id);
  assert.ok(published&&estimated);assert.equal(published.publishedAtControl,true);assert.equal(published.at,service.minutes);
  assert.equal(estimated.publishedAtControl,false);assert.equal(R.clock(estimated.at).time,'08:40');assert.equal(estimated.finalDestination,'loc-1219');
});
test('el control permite combinar varias líneas de una empresa y corredor',()=>{
  const groups=new Map();
  for(const model of engine.models)for(const [index,stop] of model.stops.entries()){
    if(index>0&&!Number.isFinite(stop.arrival_offset))continue;
    const key=[model.service.corridor,model.service.company,stop.place_id].join('|');
    if(!groups.has(key))groups.set(key,new Set());groups.get(key).add(model.service.line);
  }
  const [key,lineSet]=[...groups].find(([,lines])=>lines.size>=2),[corridor,company,location]=key.split('|'),lines=[...lineSet].slice(0,2);
  const one=engine.control({location,corridor,company,lines:[lines[0]]}),two=engine.control({location,corridor,company,lines:[lines[1]]}),both=engine.control({location,corridor,company,lines});
  assert.ok(one.length&&two.length);assert.deepEqual(new Set(both.map(r=>r.key)),new Set(one.concat(two).map(r=>r.key)));
});
test('el control permite combinar varias empresas en una localidad',()=>{
  const location='loc-1177',companies=['EDER SERVICIO DIFERENCIAL','INTERCORDOBA S.A.'];
  const one=engine.control({location,companies:[companies[0]]}),two=engine.control({location,companies:[companies[1]]}),both=engine.control({location,companies});
  assert.ok(one.length&&two.length);assert.deepEqual(new Set(both.map(r=>r.key)),new Set(one.concat(two).map(r=>r.key)));
  assert.deepEqual(new Set(both.map(r=>r.service.company)),new Set(companies));
});
test('el control acumula Ida y Vuelta sin duplicar servicios',()=>{
  const location='loc-1177';
  const ida=engine.control({location,directions:['I']}),vuelta=engine.control({location,directions:['V']}),ambos=engine.control({location,directions:['I','V']});
  assert.ok(ida.length&&vuelta.length);assert.ok(ida.every(record=>record.service.direction==='I'));assert.ok(vuelta.every(record=>record.service.direction==='V'));
  assert.deepEqual(new Set(ambos.map(record=>record.key)),new Set(ida.concat(vuelta).map(record=>record.key)));
  assert.equal(ambos.length,new Set(ambos.map(record=>record.key)).size);
});
test('el control filtra por día de paso y franja horaria',()=>{
  const records=engine.control({location:'loc-1177',company:'EDER SERVICIO DIFERENCIAL',lines:['CÓRDOBA - LA GRANJA'],day:1,fromMinute:8*60,toMinute:9*60});
  assert.ok(records.length);assert.ok(records.every(r=>r.days.includes(1)&&r.minute>=480&&r.minute<=540));
});
