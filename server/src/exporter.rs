use std::{
    collections::BTreeMap,
    fs::File,
    io::{BufWriter, Write as _},
    path::Path,
};

use anyhow::Result;
use native_db::{Database, db_type};
use serde::{Deserialize, Serialize};

#[allow(unused)]
#[derive(Serialize, Deserialize, Debug)]
struct ExportV1 {
    format: &'static str,
    version: u32,
    counts: BTreeMap<&'static str, usize>,
    config: Vec<mlm_db::Config>,
    torrents: Vec<mlm_db::Torrent>,
    selected_torrents: Vec<mlm_db::SelectedTorrent>,
    duplicate_torrents: Vec<mlm_db::DuplicateTorrent>,
    errored_torrents: Vec<mlm_db::ErroredTorrent>,
    events: Vec<mlm_db::Event>,
    lists: Vec<mlm_db::List>,
    list_items: Vec<mlm_db::ListItem>,
}

#[allow(unused)]
pub fn export_db(db: &Database<'_>, output_path: &Path) -> Result<()> {
    let r = db.r_transaction()?;
    let config = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::Config>, db_type::Error>>()?;
    let torrents = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::Torrent>, db_type::Error>>()?;
    let selected_torrents = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::SelectedTorrent>, db_type::Error>>()?;
    let duplicate_torrents = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::DuplicateTorrent>, db_type::Error>>()?;
    let errored_torrents = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::ErroredTorrent>, db_type::Error>>()?;
    let events = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::Event>, db_type::Error>>()?;
    let lists = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::List>, db_type::Error>>()?;
    let list_items = r
        .scan()
        .primary()?
        .all()?
        .collect::<Result<Vec<mlm_db::ListItem>, db_type::Error>>()?;

    let counts = BTreeMap::from([
        ("config", config.len()),
        ("torrents", torrents.len()),
        ("selected_torrents", selected_torrents.len()),
        ("duplicate_torrents", duplicate_torrents.len()),
        ("errored_torrents", errored_torrents.len()),
        ("events", events.len()),
        ("lists", lists.len()),
        ("list_items", list_items.len()),
    ]);
    let export = ExportV1 {
        format: "mlm-native-db-export",
        version: 1,
        counts,
        config,
        torrents,
        selected_torrents,
        duplicate_torrents,
        errored_torrents,
        events,
        lists,
        list_items,
    };

    let file = File::create(output_path)?;
    let mut writer = BufWriter::new(file);
    serde_json::to_writer_pretty(&mut writer, &export)?;
    writer.flush()?;

    Ok(())
}
