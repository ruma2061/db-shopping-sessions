#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;
using i64 = std::int64_t;
constexpr i64 DAY = 86400000, WEEK = 7 * DAY, GAP = 1800000;

// Explicit unsigned-byte ordering, independent of locale and signed char.
bool bytes_less(std::string_view a, std::string_view b) {
    return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end(),
        [](unsigned char x, unsigned char y) { return x < y; });
}
std::optional<i64> number(std::string_view s, i64 limit) {
    if (s.empty()) return {};
    i64 n = 0;
    for (char c : s) {
        if (c < '0' || c > '9' || n > (limit - (c - '0')) / 10) return {};
        n = n * 10 + c - '0';
        if (n > limit) return {};
    }
    return n;
}
std::optional<i64> timestamp(std::string_view s) {
    if (auto n = number(s, std::numeric_limits<i64>::max())) return n;
    if (s.size() != 20 && s.size() != 25) return {};
    if (s[4]!='-' || s[7]!='-' || s[10]!='T' || s[13]!=':' || s[16]!=':') return {};
    auto y=number(s.substr(0,4),9999), m=number(s.substr(5,2),12), d=number(s.substr(8,2),31);
    auto h=number(s.substr(11,2),23), mi=number(s.substr(14,2),59), sec=number(s.substr(17,2),59);
    if (!y || !m || !d || !h || !mi || !sec || *y<1 || *m<1 || *d<1) return {};
    const std::chrono::year_month_day date{std::chrono::year{int(*y)},
        std::chrono::month{unsigned(*m)}, std::chrono::day{unsigned(*d)}};
    if (!date.ok()) return {};
    i64 offset=0;
    if (s.size()==20) { if (s[19]!='Z') return {}; }
    else {
        if ((s[19]!='+' && s[19]!='-') || s[22]!=':') return {};
        auto oh=number(s.substr(20,2),23), om=number(s.substr(23,2),59);
        if (!oh || !om) return {};
        offset=(*oh*60+*om)*60000*(s[19]=='+' ? 1 : -1);
    }
    return std::chrono::sys_days{date}.time_since_epoch().count()*DAY
        + (*h*3600+*mi*60+*sec)*1000-offset;
}

std::optional<std::vector<std::string>> csv(std::string_view line, size_t count) {
    std::vector<std::string> fields;
    size_t start=0;
    while (true) {
        auto end=line.find(',',start);
        auto value=line.substr(start,end==line.npos ? line.size()-start : end-start);
        if (value.size()>=2 && value.front()=='"' && value.back()=='"')
            value=value.substr(1,value.size()-2);
        if (value.find('"')!=value.npos) return {};
        fields.emplace_back(value);
        if (end==line.npos) break;
        start=end+1;
    }
    if (fields.size()!=count) return {};
    return fields;
}
template<class F> void read_file(const fs::path& path, size_t count, F consume) {
    std::ifstream input(path,std::ios::binary);
    if (!input) throw std::runtime_error("Cannot read "+path.string());
    std::string line;
    std::getline(input,line); // Each input file has its own header.
    while (std::getline(input,line)) if (auto row=csv(line,count)) consume(*row);
    if (input.bad()) throw std::runtime_error("Read failed: "+path.string());
}
template<class F> void read_parts(const fs::path& dir, size_t count, F consume) {
    for (const auto& file:fs::directory_iterator(dir))
        if (file.is_regular_file() && file.path().extension()==".csv") read_file(file.path(),count,consume);
}

struct People {
    std::unordered_map<std::string,size_t> ids;
    std::vector<size_t> parent, size;
    std::vector<std::string> name;
    size_t add(const std::string& id) {
        auto [it,fresh]=ids.try_emplace(id,parent.size());
        if (fresh) { parent.push_back(it->second); size.push_back(1); name.push_back(id); }
        return it->second;
    }
    size_t root(size_t x) {
        while (parent[x]!=x) { parent[x]=parent[parent[x]]; x=parent[x]; }
        return x;
    }
    void join(const std::string& a,const std::string& b) {
        size_t x=root(add(a)), y=root(add(b));
        if (x==y) return;
        if (size[x]<size[y]) std::swap(x,y);
        parent[y]=x; size[x]+=size[y];
        if (bytes_less(name[y],name[x])) name[x]=name[y];
    }
    size_t person(const std::string& id) { return root(add(id)); }
};
struct Event { size_t person; i64 ts, revenue; std::string category; };
struct Candidate { i64 ingest; std::optional<Event> event; };
struct Click { size_t person; i64 ts; std::string id,campaign; };
struct Session { i64 start,day,events=0,revenue=0; };
struct Daily { i64 sessions=0,events=0,revenue=0; std::map<std::string,i64> categories; };
struct Campaign { i64 sessions=0,purchasing=0,revenue=0,assists=0; };
i64 day_of(i64 t) { return t/DAY-(t<0 && t%DAY!=0); }
std::string quote(const std::string& s) { return '"'+s+'"'; }

int run(const fs::path& data,const fs::path& scratch,i64 first,i64 last) {
    People people;
    std::unordered_map<std::string,std::string> products,channels;
    read_file(data/"products.csv",2,[&](const auto& r){products[r[0]]=r[1];});
    read_file(data/"campaigns.csv",2,[&](const auto& r){channels[r[0]]=r[1];});
    channels["organic"]="organic";
    read_parts(data/"links",2,[&](const auto& r){if (!r[0].empty() && !r[1].empty()) people.join(r[0],r[1]);});
    std::unordered_map<std::string,Candidate> candidates;
    read_parts(data/"events",7,[&](const auto& r){
        if (r[0].empty()) return;
        auto ingest=timestamp(r[3]);
        if (!ingest) throw std::runtime_error("Input violates valid ingest_ts guarantee");
        auto old=candidates.find(r[0]);
        if (old!=candidates.end() && old->second.ingest<=*ingest) return;
        // Invalid winners are retained as tombstones: later valid copies cannot rescue them.
        Candidate candidate{*ingest,{}};
        auto ts=timestamp(r[2]);
        bool purchase=r[4]=="purchase";
        auto price=purchase ? number(r[6],10000000) : std::optional<i64>{0};
        if (!r[1].empty() && !r[5].empty() && ts && *ts>=first && *ts<last
            && (purchase || r[4]=="view" || r[4]=="cart") && price
            && (!purchase || *price>0) && *ingest-*ts<=DAY) {
            auto p=products.find(r[5]);
            candidate.event=Event{people.person(r[1]),*ts,*price,p==products.end()?"unknown":p->second};
        }
        candidates.insert_or_assign(r[0],std::move(candidate));
    });
    std::vector<Event> events;
    for (auto& [id,c]:candidates) if (c.event) events.push_back(std::move(*c.event));
    candidates.clear(); candidates.rehash(0);
    std::vector<Click> clicks;
    std::unordered_set<std::string> seen;
    read_parts(data/"clicks",4,[&](const auto& r){
        if (r[0].empty() || !seen.insert(r[0]).second) return;
        auto ts=timestamp(r[3]);
        if (!r[1].empty() && r[2]!="organic" && channels.contains(r[2]) && ts && *ts>=first-WEEK && *ts<last)
            clicks.push_back({people.person(r[1]),*ts,r[0],r[2]});
    });
    seen.clear(); seen.rehash(0);
    std::sort(events.begin(),events.end(),[](const auto& a,const auto& b){return std::tie(a.person,a.ts)<std::tie(b.person,b.ts);});
    std::sort(clicks.begin(),clicks.end(),[](const auto& a,const auto& b){
        if (a.person!=b.person) return a.person<b.person;
        if (a.ts!=b.ts) return a.ts<b.ts;
        // Largest ID first makes the last eligible click the smallest tied ID.
        return bytes_less(b.id,a.id);
    });
    fs::create_directories(scratch);
    std::ofstream users(scratch/"user_daily.csv",std::ios::binary);
    users.exceptions(std::ios::badbit|std::ios::failbit);
    users << "user_id,day,sessions,events,revenue_cents,top_category,has_purchase\n";
    std::map<std::pair<std::string,i64>,Campaign> totals;
    size_t cb=0;
    for (size_t begin=0;begin<events.size();) {
        size_t end=begin+1,person=events[begin].person;
        while (end<events.size() && events[end].person==person) ++end;
        std::vector<Session> sessions;
        std::map<i64,Daily> days;
        i64 previous=0;
        for (size_t i=begin;i<end;++i) {
            const auto& e=events[i];
            if (i==begin || e.ts-previous>GAP) {
                sessions.push_back({e.ts,day_of(e.ts)});
                ++days[sessions.back().day].sessions;
            }
            auto& s=sessions.back(); auto& d=days[s.day];
            ++s.events; s.revenue+=e.revenue; ++d.events; d.revenue+=e.revenue;
            if (e.revenue) d.categories[e.category]+=e.revenue;
            previous=e.ts;
        }
        for (const auto& [day,d]:days) {
            std::string top; i64 best=-1;
            for (const auto& [cat,revenue]:d.categories)
                if (revenue>best || (revenue==best && bytes_less(cat,top))) {top=cat;best=revenue;}
            users << quote(people.name[person]) << ',' << day << ',' << d.sessions << ',' << d.events
                << ',' << d.revenue << ',' << quote(top) << ',' << (best>=0) << '\n';
        }
        while (cb<clicks.size() && clicks[cb].person<person) ++cb;
        size_t ce=cb;
        while (ce<clicks.size() && clicks[ce].person==person) ++ce;
        size_t eligible=cb;
        for (const auto& s:sessions) {
            while (eligible<ce && clicks[eligible].ts<=s.start) ++eligible;
            std::string campaign="organic";
            if (eligible>cb && s.start-clicks[eligible-1].ts<=WEEK) campaign=clicks[eligible-1].campaign;
            auto& total=totals[{campaign,s.day}];
            ++total.sessions; total.purchasing+=(s.revenue>0); total.revenue+=s.revenue;
        }
        // Campaign interval union. No click/session cross product is materialized.
        std::vector<size_t> day_end(sessions.size());
        for (size_t i=0;i<sessions.size();) {
            size_t j=i+1;
            while (j<sessions.size() && sessions[j].day==sessions[i].day) ++j;
            for (size_t k=i;k<j;++k) day_end[k]=j;
            i=j;
        }
        std::unordered_map<std::string,size_t> covered;
        size_t left=0,right=0;
        for (size_t i=cb;i<ce;++i) {
            const auto& c=clicks[i];
            while (left<sessions.size() && sessions[left].start<c.ts) ++left;
            while (right<sessions.size() && sessions[right].start<=c.ts+WEEK) ++right;
            size_t at=std::max(left,covered[c.campaign]);
            while (at<right) {
                size_t next=std::min(right,day_end[at]);
                totals[{c.campaign,sessions[at].day}].assists+=static_cast<i64>(next-at);
                at=next;
            }
            covered[c.campaign]=std::max(covered[c.campaign],right);
        }
        cb=ce; begin=end;
    }
    users.close();
    std::ofstream campaigns(scratch/"campaign_daily.csv",std::ios::binary);
    campaigns.exceptions(std::ios::badbit|std::ios::failbit);
    campaigns << "campaign_id,channel,day,sessions,purchasing_sessions,revenue_cents,assisted_sessions\n";
    for (const auto& [key,t]:totals)
        campaigns << quote(key.first) << ',' << quote(channels.at(key.first)) << ',' << key.second << ','
            << t.sessions << ',' << t.purchasing << ',' << t.revenue << ',' << t.assists << '\n';
    campaigns.close();
    std::cerr << "events=" << events.size() << " clicks=" << clicks.size() << " people=" << people.ids.size() << '\n';
    return 0;
}
int main(int argc,char** argv) {
    try {
        if (argc!=5) throw std::runtime_error("Usage: sessions-core DATA_DIR SCRATCH_DIR WINDOW_START WINDOW_END");
        auto first=timestamp(argv[3]), last=timestamp(argv[4]);
        if (!first || !last || *first>=*last || *first<-62135596800000LL || *last>253402300799999LL)
            throw std::runtime_error("Invalid manifest window");
        auto started=std::chrono::steady_clock::now();
        int result=run(argv[1],argv[2],*first,*last);
        std::cerr << "core_seconds=" << std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count() << '\n';
        return result;
    } catch (const std::exception& e) { std::cerr << "error: " << e.what() << '\n'; return 1; }
}
