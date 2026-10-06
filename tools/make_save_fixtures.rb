# Generates fake-but-structurally-faithful VX Ace / VX saves and databases with REAL Ruby Marshal.
#   ruby tools/make_save_fixtures.rb tests/fixtures/rgss
require 'fileutils'
out = ARGV[0] || 'tests/fixtures/rgss'
FileUtils.mkdir_p(File.join(out, 'ace_game/Data'))
FileUtils.mkdir_p(File.join(out, 'vx_game/Data'))

module RPG; end
%w[System Actor Item Weapon Armor MapInfo BGM Class].each { |n| RPG.const_set(n, Class.new) }
%w[Game_Switches Game_Variables Game_SelfSwitches Game_System Game_Message Game_Actors Game_Actor Game_Party Game_Troop
   Game_Map Game_Player Game_Screen Game_Timer Game_Picture].each { |n| Object.const_set(n, Class.new) }

def mk(klass, iv) o = klass.new; iv.each { |k, v| o.instance_variable_set(k, v) }; o end
def s(t) t.dup end

switches = [nil, true, false, true, false]
variables = [nil, 10, 0, 250, nil, 'text']
self_sw = { [1, 2, 'A'] => true }
party_items = { 1 => 5, 3 => 2 }

def actor(ace, id, name, lvl)
  exp = ace ? { 1 => lvl * 100 } : lvl * 100
  mk(Game_Actor, { '@actor_id' => id, '@name' => name, '@level' => lvl, '@exp' => exp, '@hp' => 200 + id, '@mp' => 30,
                   '@class_id' => 1, '@param_plus' => [0] * 8 })
end

def build(ace, sw, vars, self_sw, items)
  actors = [nil, actor(ace, 1, 'アレックス', 5), actor(ace, 2, 'Mia', 3)]
  party = mk(Game_Party, { '@gold' => 1234, '@items' => items, '@weapons' => { 1 => 1 }, '@armors' => {}, '@actors' => [1, 2],
                           '@steps' => 99 })
  player = mk(Game_Player, { '@x' => 7, '@y' => 9, '@real_x' => ace ? 7.0 : 7 * 256, '@real_y' => ace ? 9.0 : 9 * 256,
                             '@transferring' => false, '@direction' => 2 })
  map = mk(Game_Map, { '@map_id' => 3, '@display_x' => ace ? 0.0 : 0 })
  system = mk(Game_System, { '@save_count' => 3, '@version_id' => 1 })
  [system, mk(Game_Message, { '@texts' => [] }), mk(Game_Switches, { '@data' => sw }), mk(Game_Variables, { '@data' => vars }),
   mk(Game_SelfSwitches, { '@data' => self_sw }), mk(Game_Actors, { '@data' => actors }), party, mk(Game_Troop, { '@interpreter' => nil }),
   map, player, mk(Game_Screen, { '@tone' => nil }), mk(Game_Timer, { '@count' => 0 })]
end

# ---- Ace: header stream + contents hash
o = build(true, switches, variables, self_sw, party_items)
header = { characters: [['Actor1', 0], ['Actor2', 1]], playtime_s: 4321 }
contents = { system: o[0], timer: o[11], message: o[1], switches: o[2], variables: o[3], self_switches: o[4], actors: o[5],
             party: o[6], troop: o[7], map: o[8], player: o[9], screen: o[10] }
File.binwrite(File.join(out, 'ace_game/Save01.rvdata2'), Marshal.dump(header) + Marshal.dump(contents))

# ---- VX: positional streams, no encoding ivars needed
v = build(false, switches.map { |x| x }, variables, self_sw, party_items)
streams = [[['Actor1', 0]], 12345, mk(RPG::BGM, { '@name' => '' }), mk(RPG::BGM, { '@name' => '' }), v[0], v[1], v[2], v[3], v[4],
           v[5], v[6], v[7], v[8], v[9]]
File.binwrite(File.join(out, 'vx_game/Save1.rvdata'), streams.map { |x| Marshal.dump(x) }.join)

# ---- databases (RPG::* objects with @name)
def named(klass, names) [nil] + names.each_with_index.map { |n, i| mk(klass, { '@id' => i + 1, '@name' => n }) } end
sysobj = mk(RPG::System, { '@switches' => [nil, 'ドア開放', 'ボス撃破', 'Chest opened', ''], '@variables' => [nil, '所持金', 'Steps', '', '', 'Text var'],
                           '@currency_unit' => 'G' })
%w[ace_game/Data/System.rvdata2 vx_game/Data/System.rvdata].each { |f| File.binwrite(File.join(out, f), Marshal.dump(sysobj)) }
{ 'Actors' => named(RPG::Actor, %w[アレックス Mia]), 'Items' => named(RPG::Item, %w[ポーション Ether 万能薬]),
  'Weapons' => named(RPG::Weapon, %w[鉄の剣 Staff]), 'Armors' => named(RPG::Armor, %w[革の鎧]),
  'Classes' => named(RPG::Class, %w[戦士]) }.each do |n, arr|
  File.binwrite(File.join(out, "ace_game/Data/#{n}.rvdata2"), Marshal.dump(arr))
  File.binwrite(File.join(out, "vx_game/Data/#{n}.rvdata"), Marshal.dump(arr))
end
mapinfos = { 1 => mk(RPG::MapInfo, { '@name' => '村', '@parent_id' => 0 }), 3 => mk(RPG::MapInfo, { '@name' => 'Forest', '@parent_id' => 0 }) }
File.binwrite(File.join(out, 'ace_game/Data/MapInfos.rvdata2'), Marshal.dump(mapinfos))
File.binwrite(File.join(out, 'vx_game/Data/MapInfos.rvdata'), Marshal.dump(mapinfos))
File.write(File.join(out, 'ace_game/Game.ini'), "[Game]\r\nRTP=RPGVXAce\r\nLibrary=System\\RGSS301.dll\r\nScripts=Data\\Scripts.rvdata2\r\nTitle=Fixture\r\n")
File.write(File.join(out, 'vx_game/Game.ini'), "[Game]\r\nRTP=RPGVX\r\nLibrary=RGSS202E.DLL\r\nScripts=Data\\Scripts.rvdata\r\nTitle=Fixture VX\r\n")
puts 'ok'
